import json
import tempfile
from datetime import date, timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from pywebpush import WebPushException

from ponto.models import AjustePonto, FuncionarioJornada
from ponto.tests import _bater_ponto, _criar_equipamento, _criar_horario_util

from .models import (
    Documento, FichaEPI, Funcionario, InscricaoPush, Setor, SolicitacaoFerias, TermoComodato, TipoDocumento, Usuario,
)
from .pwa_views import CHAVE_SESSAO_PUSH

ENDPOINT_GOOGLE = 'https://fcm.googleapis.com/fcm/send/abc123'

PUSH_LIGADO = override_settings(
    VAPID_PUBLIC_KEY='chave-publica-de-teste',
    VAPID_PRIVATE_KEY='chave-privada-de-teste',
    PUSH_EM_SEGUNDO_PLANO=False,
)


class BasePush(TestCase):
    def setUp(self):
        self.setor = Setor.objects.create(nome='Produção')
        self.outro_setor = Setor.objects.create(nome='Expedição')
        self.colaborador = self._pessoa('Ana Colaboradora', '11111111111', 'colaborador', self.setor)
        self.gestor = self._pessoa('Bruno Gestor', '22222222222', 'gestor', self.setor)
        self.gestor_outro_setor = self._pessoa('Carla Gestora', '33333333333', 'gestor', self.outro_setor)
        self.rh = self._pessoa('Diego RH', '44444444444', 'rh', self.outro_setor)

        patcher = mock.patch('funcionarios.notificacoes.webpush')
        self.webpush = patcher.start()
        self.addCleanup(patcher.stop)

    def _pessoa(self, nome, cpf, papel, setor):
        funcionario = Funcionario.objects.create(
            nome=nome, cpf=cpf, cargo='Cargo', setor=setor, status='ativo',
            data_admissao=date.today() - timedelta(days=800),
        )
        user = User.objects.create_user(username=cpf, password='senha123')
        Usuario.objects.create(user=user, funcionario=funcionario, papel=papel)
        return user

    def _inscrever(self, user, sufixo=None):
        return InscricaoPush.objects.create(
            user=user, endpoint=f'{ENDPOINT_GOOGLE}-{sufixo or user.username}', p256dh='p', auth='a',
        )

    def _destinatarios(self):
        """Usernames que receberam push, na ordem dos envios."""
        enviados = []
        for chamada in self.webpush.call_args_list:
            endpoint = chamada.kwargs['subscription_info']['endpoint']
            enviados.append(InscricaoPush.objects.get(endpoint=endpoint).user.username)
        return enviados

    def _payloads(self):
        return [json.loads(c.kwargs['data']) for c in self.webpush.call_args_list]


@PUSH_LIGADO
class InscricaoPushViewTests(BasePush):
    def test_inscreve_o_aparelho_e_guarda_o_endpoint_na_sessao(self):
        self.client.force_login(self.colaborador)

        resposta = self.client.post(
            reverse('inscrever_push'),
            json.dumps({'endpoint': ENDPOINT_GOOGLE, 'keys': {'p256dh': 'chave', 'auth': 'segredo'}}),
            content_type='application/json',
        )

        self.assertEqual(resposta.status_code, 204)
        inscricao = InscricaoPush.objects.get(endpoint=ENDPOINT_GOOGLE)
        self.assertEqual(inscricao.user, self.colaborador)
        self.assertEqual(self.client.session[CHAVE_SESSAO_PUSH], ENDPOINT_GOOGLE)

    def test_recusa_endpoint_fora_dos_servicos_de_push(self):
        # Sem a lista de hosts, o servidor faria POST para qualquer URL
        # informada pelo navegador, inclusive da rede interna.
        self.client.force_login(self.colaborador)

        for endpoint in ('https://192.168.0.10/admin', 'http://fcm.googleapis.com/x', 'https://fcm.googleapis.com.evil.com/x'):
            resposta = self.client.post(
                reverse('inscrever_push'),
                json.dumps({'endpoint': endpoint, 'keys': {'p256dh': 'chave', 'auth': 'segredo'}}),
                content_type='application/json',
            )
            self.assertEqual(resposta.status_code, 400, endpoint)
        self.assertFalse(InscricaoPush.objects.exists())

    def test_aparelho_compartilhado_passa_para_quem_entrou_por_ultimo(self):
        self._inscrever(self.colaborador, sufixo='aparelho')
        endpoint = f'{ENDPOINT_GOOGLE}-aparelho'
        self.client.force_login(self.gestor)

        self.client.post(
            reverse('inscrever_push'),
            json.dumps({'endpoint': endpoint, 'keys': {'p256dh': 'chave', 'auth': 'segredo'}}),
            content_type='application/json',
        )

        self.assertEqual(InscricaoPush.objects.get(endpoint=endpoint).user, self.gestor)

    def test_logout_desvincula_o_aparelho(self):
        self.client.force_login(self.colaborador)
        self.client.post(
            reverse('inscrever_push'),
            json.dumps({'endpoint': ENDPOINT_GOOGLE, 'keys': {'p256dh': 'chave', 'auth': 'segredo'}}),
            content_type='application/json',
        )

        self.client.get(reverse('logout'))

        self.assertFalse(InscricaoPush.objects.exists())

    def test_cancelar_so_apaga_inscricao_do_proprio_usuario(self):
        alheia = self._inscrever(self.gestor)
        self.client.force_login(self.colaborador)

        self.client.post(
            reverse('cancelar_push'), json.dumps({'endpoint': alheia.endpoint}), content_type='application/json',
        )

        self.assertTrue(InscricaoPush.objects.filter(id=alheia.id).exists())

    def test_sem_chaves_vapid_o_convite_nao_aparece(self):
        self.client.force_login(self.colaborador)

        with override_settings(VAPID_PUBLIC_KEY='', VAPID_PRIVATE_KEY=''):
            resposta = self.client.get(reverse('meu_perfil'))

        self.assertNotContains(resposta, 'notificacoes.js')
        self.assertNotContains(resposta, 'aviso-push')


@PUSH_LIGADO
class EnvioPushTests(BasePush):
    def test_inscricao_que_o_navegador_cancelou_e_apagada(self):
        inscricao = self._inscrever(self.colaborador)
        self.webpush.side_effect = WebPushException('Gone', response=mock.Mock(status_code=410))
        solicitacao = SolicitacaoFerias.objects.create(
            funcionario=self.colaborador.usuario.funcionario, data_solicitada=date.today() + timedelta(days=40),
            status='pendente_rh',
        )
        self.client.force_login(self.rh)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('rh_concluir_ferias', args=[solicitacao.id]))

        self.assertFalse(InscricaoPush.objects.filter(id=inscricao.id).exists())

    def test_falha_de_envio_nao_derruba_a_tela(self):
        self._inscrever(self.colaborador)
        self.webpush.side_effect = WebPushException('Erro', response=mock.Mock(status_code=500))
        solicitacao = SolicitacaoFerias.objects.create(
            funcionario=self.colaborador.usuario.funcionario, data_solicitada=date.today() + timedelta(days=40),
            status='pendente_rh',
        )
        self.client.force_login(self.rh)

        with self.captureOnCommitCallbacks(execute=True):
            resposta = self.client.post(reverse('rh_concluir_ferias', args=[solicitacao.id]))

        self.assertEqual(resposta.status_code, 302)
        solicitacao.refresh_from_db()
        self.assertEqual(solicitacao.status, 'aprovada')
        self.assertEqual(InscricaoPush.objects.count(), 1)

    def test_cada_envio_usa_um_dicionario_de_claims_novo(self):
        # O pywebpush grava o `aud` do endpoint nas claims; reaproveitar o
        # mesmo dicionário mandaria o `aud` do primeiro aparelho para o segundo.
        self._inscrever(self.colaborador, sufixo='celular')
        self._inscrever(self.colaborador, sufixo='computador')
        solicitacao = SolicitacaoFerias.objects.create(
            funcionario=self.colaborador.usuario.funcionario, data_solicitada=date.today() + timedelta(days=40),
            status='pendente_rh',
        )
        self.client.force_login(self.rh)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('rh_concluir_ferias', args=[solicitacao.id]))

        claims = [c.kwargs['vapid_claims'] for c in self.webpush.call_args_list]
        self.assertEqual(len(claims), 2)
        self.assertIsNot(claims[0], claims[1])


@PUSH_LIGADO
class FeriasPushTests(BasePush):
    def setUp(self):
        super().setUp()
        for user in (self.colaborador, self.gestor, self.gestor_outro_setor, self.rh):
            self._inscrever(user)

    def test_pedido_de_ferias_avisa_so_o_gestor_do_setor(self):
        self.client.force_login(self.colaborador)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('solicitar_ferias'), {
                'data_solicitada': (date.today() + timedelta(days=45)).isoformat(),
                'dias_solicitados': 30,
            })

        self.assertEqual(self._destinatarios(), [self.gestor.username])
        self.assertEqual(self._payloads()[0]['url'], reverse('gestor_listar_ferias'))

    def test_gestor_aprova_avisa_colaborador_e_rh(self):
        solicitacao = SolicitacaoFerias.objects.create(
            funcionario=self.colaborador.usuario.funcionario, data_solicitada=date.today() + timedelta(days=40),
        )
        self.client.force_login(self.gestor)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('gestor_aprovar_ferias', args=[solicitacao.id]))

        self.assertCountEqual(self._destinatarios(), [self.colaborador.username, self.rh.username])

    def test_sugestao_do_gestor_avisa_o_colaborador(self):
        solicitacao = SolicitacaoFerias.objects.create(
            funcionario=self.colaborador.usuario.funcionario, data_solicitada=date.today() + timedelta(days=40),
        )
        self.client.force_login(self.gestor)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('gestor_sugerir_ferias', args=[solicitacao.id]), {
                'data_sugerida': (date.today() + timedelta(days=60)).isoformat(),
                'dias_sugeridos': 20,
                'observacao_gestor': 'Pico de produção',
            })

        self.assertEqual(self._destinatarios(), [self.colaborador.username])
        self.assertIn('20 dias', self._payloads()[0]['corpo'])

    def test_rh_confirma_avisa_o_colaborador(self):
        solicitacao = SolicitacaoFerias.objects.create(
            funcionario=self.colaborador.usuario.funcionario, data_solicitada=date.today() + timedelta(days=40),
            status='pendente_rh',
        )
        self.client.force_login(self.rh)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('rh_concluir_ferias', args=[solicitacao.id]))

        self.assertEqual(self._destinatarios(), [self.colaborador.username])
        self.assertEqual(self._payloads()[0]['titulo'], 'Férias confirmadas')


@PUSH_LIGADO
class DocumentosEComodatoPushTests(BasePush):
    def setUp(self):
        super().setUp()
        self._inscrever(self.colaborador)
        self.funcionario = self.colaborador.usuario.funcionario

    def test_documento_rejeitado_avisa_o_colaborador_com_o_motivo(self):
        tipo = TipoDocumento.objects.create(nome='RG', obrigatorio=True)
        with self.settings(MEDIA_ROOT=tempfile.mkdtemp()):
            documento = Documento.objects.create(
                funcionario=self.funcionario, tipo_documento=tipo, arquivo=ContentFile(b'x', name='rg.pdf'),
            )
        self.client.force_login(self.rh)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('rejeitar_documento', args=[documento.id]), {'motivo': 'Foto ilegível'})

        self.assertEqual(self._destinatarios(), [self.colaborador.username])
        self.assertIn('Foto ilegível', self._payloads()[0]['corpo'])

    def test_comodato_rejeitado_avisa_o_colaborador(self):
        comodato = TermoComodato.objects.create(
            funcionario=self.funcionario, modalidade='uniforme', data_emprestimo=date.today(), status='em_validacao',
        )
        self.client.force_login(self.rh)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('rejeitar_comodato', args=[comodato.id]), {'motivo': 'Falta assinatura'})

        self.assertEqual(self._destinatarios(), [self.colaborador.username])
        self.assertEqual(self._payloads()[0]['url'], reverse('meus_documentos'))

    def test_lembrete_diario_lista_as_pendencias(self):
        TipoDocumento.objects.create(nome='RG', obrigatorio=True)
        TipoDocumento.objects.create(nome='CPF', obrigatorio=True)
        TermoComodato.objects.create(
            funcionario=self.funcionario, modalidade='uniforme', data_emprestimo=date.today(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            call_command('lembrar_pendencias', stdout=StringIO())

        self.assertEqual(self._destinatarios(), [self.colaborador.username])
        self.assertEqual(
            self._payloads()[0]['corpo'], '2 documentos para enviar e 1 termo de comodato para assinar.',
        )

    def test_lembrete_espera_o_envio_terminar(self):
        # Em produção o push sai numa thread daemon; num comando ela morreria
        # junto com o processo, então o lembrete envia sem thread.
        TermoComodato.objects.create(
            funcionario=self.funcionario, modalidade='uniforme', data_emprestimo=date.today(),
        )

        with override_settings(PUSH_EM_SEGUNDO_PLANO=True), \
                mock.patch('funcionarios.notificacoes.threading.Thread') as thread, \
                self.captureOnCommitCallbacks(execute=True):
            call_command('lembrar_pendencias', stdout=StringIO())

        thread.assert_not_called()
        self.assertEqual(self._destinatarios(), [self.colaborador.username])

    def test_lembrete_nao_avisa_quem_esta_em_dia(self):
        with self.captureOnCommitCallbacks(execute=True):
            call_command('lembrar_pendencias', stdout=StringIO())

        self.webpush.assert_not_called()


@PUSH_LIGADO
class AjustePontoPushTests(BasePush):
    def setUp(self):
        super().setUp()
        for user in (self.colaborador, self.gestor, self.gestor_outro_setor, self.rh):
            self._inscrever(user)

    def test_pedido_de_ajuste_avisa_quem_aprova(self):
        # O RH e o gestor da equipe; a gestora de outro setor não.
        self.client.force_login(self.colaborador)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('ponto:solicitar_ajuste_ponto'), {
                'data': (timezone.now().date() - timedelta(days=1)).isoformat(),
                'tipo_evento': 'entrada',
                'horario_ajustado': '08:00',
                'motivo': 'Esqueci de bater',
            })

        self.assertTrue(AjustePonto.objects.exists())
        self.assertCountEqual(self._destinatarios(), [self.rh.username, self.gestor.username])
        self.assertEqual({p['url'] for p in self._payloads()}, {reverse('ponto:ajustes_pendentes')})

    def test_rejeicao_avisa_o_colaborador_com_o_motivo(self):
        ajuste = AjustePonto.objects.create(
            funcionario=self.colaborador.usuario.funcionario, data=date.today() - timedelta(days=1),
            tipo_evento='entrada', horario_ajustado='08:00', motivo='Esqueci',
        )
        self.client.force_login(self.rh)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('ponto:rejeitar_ajuste', args=[ajuste.id]), {'observacao_rh': 'Sem justificativa'})

        self.assertEqual(self._destinatarios(), [self.colaborador.username])
        self.assertEqual(self._payloads()[0]['titulo'], 'Ajuste de ponto recusado')
        self.assertIn('Sem justificativa', self._payloads()[0]['corpo'])


@PUSH_LIGADO
class IntervaloCurtoPushTests(BasePush):
    """`manage.py avisar_intervalos_curtos`: um aviso por almoço abaixo de 1h, pro RH e pro gestor da equipe."""

    def setUp(self):
        super().setUp()
        self.funcionario = self.colaborador.usuario.funcionario
        self.equipamento = _criar_equipamento()
        FuncionarioJornada.objects.create(
            funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=_criar_horario_util(4801),
        )
        self.dia = date(2026, 8, 3)
        for user in (self.colaborador, self.gestor, self.gestor_outro_setor, self.rh):
            self._inscrever(user)

    def _rodar(self, *argumentos):
        saida = StringIO()
        with self.captureOnCommitCallbacks(execute=True):
            call_command('avisar_intervalos_curtos', '--data', self.dia.isoformat(), *argumentos, stdout=saida)
        return saida.getvalue()

    def test_almoco_curto_avisa_o_rh_e_o_gestor_da_equipe(self):
        _bater_ponto(self.funcionario, self.equipamento, self.dia, '08:00', '12:00', '12:40', '17:20')

        self._rodar()

        self.assertCountEqual(self._destinatarios(), [self.rh.username, self.gestor.username])
        payloads = {p['url'].split('?')[0]: p for p in self._payloads()}
        rh = payloads[reverse('ponto:listar_calculo')]
        gestor = payloads[reverse('ponto:ponto_equipe_funcionario', args=[self.funcionario.id])]
        self.assertEqual(rh['corpo'], 'Ana Colaboradora tirou 40 min de intervalo em 03/08 (12:00 às 12:40).')
        self.assertIn(f'funcionario={self.funcionario.id}', rh['url'])
        self.assertIn('data_inicio=2026-08-03', gestor['url'])

    def test_responsavel_pelo_setor_tambem_recebe(self):
        # Carla é gestora de outro setor, mas é a responsável pelo setor da Ana.
        self.setor.gestor = self.gestor_outro_setor.usuario.funcionario
        self.setor.save()
        _bater_ponto(self.funcionario, self.equipamento, self.dia, '08:00', '12:00', '12:40', '17:20')

        self._rodar()

        self.assertCountEqual(
            self._destinatarios(), [self.rh.username, self.gestor.username, self.gestor_outro_setor.username],
        )

    def test_quem_e_rh_e_gestor_recebe_uma_vez(self):
        Usuario.objects.filter(user=self.gestor).update(acesso_rh=True)
        _bater_ponto(self.funcionario, self.equipamento, self.dia, '08:00', '12:00', '12:40', '17:20')

        self._rodar()

        self.assertCountEqual(self._destinatarios(), [self.rh.username, self.gestor.username])

    def test_jornada_de_ate_6h_avisa_abaixo_de_15_min(self):
        _bater_ponto(self.funcionario, self.equipamento, self.dia, '08:00', '11:00', '11:10', '13:30')  # 5h20

        saida = self._rodar()

        self.assertEqual({p['titulo'] for p in self._payloads()}, {'Intervalo abaixo de 15 min'})
        self.assertIn('Ana Colaboradora: 10 min (11:00 às 11:10), mínimo 15 min', saida)

    def test_almoco_de_1h_nao_avisa(self):
        _bater_ponto(self.funcionario, self.equipamento, self.dia, '08:00', '12:00', '13:00', '17:00')

        saida = self._rodar()

        self.webpush.assert_not_called()
        self.assertIn('0 caso(s)', saida)

    def test_dry_run_so_lista(self):
        _bater_ponto(self.funcionario, self.equipamento, self.dia, '08:00', '12:00', '12:40', '17:20')

        saida = self._rodar('--dry-run')

        self.webpush.assert_not_called()
        self.assertIn('Ana Colaboradora: 40 min (12:00 às 12:40)', saida)

    def test_comando_espera_o_envio_terminar(self):
        # Em produção o push sai numa thread daemon; num comando ela morreria
        # junto com o processo, então o comando envia sem thread.
        _bater_ponto(self.funcionario, self.equipamento, self.dia, '08:00', '12:00', '12:40', '17:20')

        with override_settings(PUSH_EM_SEGUNDO_PLANO=True), mock.patch('funcionarios.notificacoes.threading.Thread') as thread:
            self._rodar()

        thread.assert_not_called()
        self.assertEqual(self.webpush.call_count, 2)


@PUSH_LIGADO
class DocumentoAssinadoPushTests(BasePush):
    """Colaborador devolve um documento assinado: quem valida é avisado."""

    def setUp(self):
        super().setUp()
        self.seguranca = self._pessoa('Elisa Segurança', '55555555555', 'seguranca_trabalho', self.outro_setor)
        for user in (self.colaborador, self.gestor, self.rh, self.seguranca):
            self._inscrever(user)
        self.funcionario = self.colaborador.usuario.funcionario
        self.client.force_login(self.colaborador)
        media = self.settings(MEDIA_ROOT=tempfile.mkdtemp())
        media.enable()
        self.addCleanup(media.disable)

    def _pdf(self):
        return SimpleUploadedFile('assinado.pdf', b'%PDF-1.4', content_type='application/pdf')

    def test_contrato_assinado_avisa_o_rh(self):
        tipo = TipoDocumento.objects.create(nome='Contrato de Trabalho', obrigatorio=True, eh_contrato=True)
        self.funcionario.status = 'aguardando_assinatura'
        self.funcionario.save()

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('enviar_documento'), {
                'tipo_documento': tipo.id, 'arquivo': self._pdf(), 'origem': 'assinatura_gov',
            })

        self.assertEqual(self._destinatarios(), [self.rh.username])
        self.assertEqual(self._payloads()[0]['url'], reverse('detalhe_funcionario', args=[self.funcionario.id]))

    def test_documento_comum_nao_avisa_ninguem(self):
        tipo = TipoDocumento.objects.create(nome='RG', obrigatorio=True)

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('enviar_documento'), {'tipo_documento': tipo.id, 'arquivo': self._pdf()})

        self.assertTrue(Documento.objects.filter(tipo_documento=tipo).exists())
        self.webpush.assert_not_called()

    def test_comodato_assinado_avisa_o_rh(self):
        comodato = TermoComodato.objects.create(
            funcionario=self.funcionario, modalidade='uniforme', data_emprestimo=date.today(),
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('reenviar_comodato_assinado', args=[comodato.id]), {'arquivo_assinado': self._pdf()})

        self.assertEqual(self._destinatarios(), [self.rh.username])

    def test_ficha_epi_assinada_avisa_seguranca_e_rh(self):
        ficha = FichaEPI.objects.create(
            funcionario=self.funcionario, data_entrega=date.today(),
            arquivo_gerado=ContentFile(b'%PDF-1.4', name='ficha.pdf'),
        )

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('reenviar_epi_assinado', args=[ficha.id]), {'arquivo_assinado': self._pdf()})

        self.assertCountEqual(self._destinatarios(), [self.rh.username, self.seguranca.username])
