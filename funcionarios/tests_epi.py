import shutil
import tempfile
from datetime import date, timedelta
from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    FichaEPI, Funcionario, ItemCatalogoEPI, ItemEPI, MatrizEpiSetor, OcorrenciaEPI, Setor, Usuario,
)

MEDIA_TESTE = tempfile.mkdtemp()


def _criar_usuario(nome, cpf, papel, setor, cargo='Analista', acesso_rh=False):
    funcionario = Funcionario.objects.create(nome=nome, cpf=cpf, cargo=cargo, setor=setor, status='ativo')
    user = User.objects.create_user(username=cpf, password='senha123')
    Usuario.objects.create(user=user, funcionario=funcionario, papel=papel, acesso_rh=acesso_rh)
    return user


class DivisaoPerfisTests(TestCase):
    """DP opera os módulos já existentes; RH (papel ou acesso_rh) também vê os exclusivos."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='Administrativo')
        self.user_dp = _criar_usuario('DP Teste', '11111111111', 'dp', self.setor)
        self.user_rh = _criar_usuario('RH Teste', '22222222222', 'rh', self.setor)
        self.user_seguranca = _criar_usuario('Tecnico SST', '33333333333', 'seguranca_trabalho', self.setor)
        self.colaborador = Funcionario.objects.create(
            nome='Colaborador', cpf='44444444444', cargo='Operador', setor=self.setor, status='ativo',
        )
        User.objects.create_user(username='44444444444', password='senha123')
        Usuario.objects.create(
            user=User.objects.get(username='44444444444'), funcionario=self.colaborador, papel='colaborador',
        )

    def test_dp_acessa_os_modulos_existentes(self):
        self.client.force_login(self.user_dp)
        for nome in ('listar_funcionarios', 'listar_comodatos', 'concluir_ferias', 'rh_vencimento_ferias',
                     'ponto:listar_equipamentos', 'terceiros:listar_empresas'):
            with self.subTest(nome=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)

    def test_dp_edita_dados_do_funcionario(self):
        # Checagem interna da view (não só o decorator) também precisa aceitar o DP.
        self.client.force_login(self.user_dp)
        response = self.client.get(reverse('detalhe_funcionario', args=[self.colaborador.id]))
        self.assertContains(response, reverse('editar_dados_funcionario', args=[self.colaborador.id]))

    def _salvar_cargo(self, cargo):
        return self.client.post(reverse('editar_dados_funcionario', args=[self.colaborador.id]), {
            'matricula': '', 'codigo_empresa': 453, 'cargo': cargo, 'setor': self.setor.id,
            'data_admissao': '', 'salario': '',
        })

    def test_rh_altera_cargo_com_sugestoes_dos_cargos_existentes(self):
        self.client.force_login(self.user_rh)
        response = self.client.get(reverse('detalhe_funcionario', args=[self.colaborador.id]))
        self.assertContains(response, 'list="cargos-existentes"')
        self.assertContains(response, '<option value="Analista">')

        self._salvar_cargo('Supervisor de Produção')
        self.colaborador.refresh_from_db()
        self.assertEqual(self.colaborador.cargo, 'Supervisor de Produção')

    def test_cargo_digitado_reaproveita_a_grafia_existente(self):
        self.client.force_login(self.user_rh)
        self._salvar_cargo('  analista ')
        self.colaborador.refresh_from_db()
        self.assertEqual(self.colaborador.cargo, 'Analista')

    def test_colaborador_nao_altera_cargo(self):
        self.client.force_login(User.objects.get(username='44444444444'))
        self._salvar_cargo('Diretor')
        self.colaborador.refresh_from_db()
        self.assertEqual(self.colaborador.cargo, 'Operador')

    def test_dp_nao_concede_acesso_rh(self):
        self.client.force_login(self.user_dp)
        self.client.get(reverse('alternar_acesso_rh', args=[self.colaborador.id]))
        self.assertFalse(Usuario.objects.get(funcionario=self.colaborador).acesso_rh)

    def test_rh_concede_acesso_rh(self):
        self.client.force_login(self.user_rh)
        self.client.get(reverse('alternar_acesso_rh', args=[self.colaborador.id]))
        self.assertTrue(Usuario.objects.get(funcionario=self.colaborador).acesso_rh)

    def test_dp_nao_ve_o_modulo_de_epi(self):
        self.client.force_login(self.user_dp)
        self.assertRedirects(self.client.get(reverse('listar_fichas_epi')), reverse('dashboard'),
                             fetch_redirect_response=False)

    def test_acesso_rh_libera_epi(self):
        user = _criar_usuario('Gestor RH', '55555555555', 'gestor', self.setor, acesso_rh=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('listar_fichas_epi')).status_code, 200)

    def test_seguranca_nao_ve_modulos_de_dp(self):
        self.client.force_login(self.user_seguranca)
        self.assertRedirects(self.client.get(reverse('listar_funcionarios')), reverse('dashboard'),
                             fetch_redirect_response=False)

    def test_dashboard_da_seguranca_vai_para_as_fichas(self):
        self.client.force_login(self.user_seguranca)
        self.assertRedirects(self.client.get(reverse('dashboard')), reverse('listar_fichas_epi'),
                             fetch_redirect_response=False)

    def test_menu_da_seguranca_mostra_so_epi_e_meu_espaco(self):
        self.client.force_login(self.user_seguranca)
        response = self.client.get(reverse('listar_fichas_epi'))
        self.assertContains(response, reverse('gerenciar_matriz_epi'))
        self.assertNotContains(response, reverse('listar_funcionarios'))
        self.assertContains(response, reverse('meus_documentos'))


class MigracaoRhViraDpTests(TestCase):
    def test_rh_vira_dp_e_volta(self):
        setor = Setor.objects.create(nome='RH')
        _criar_usuario('Antigo RH', '66666666666', 'rh', setor)
        migracao = import_module('funcionarios.migrations.0032_rh_vira_dp')

        migracao.rh_vira_dp(django_apps, None)
        self.assertEqual(Usuario.objects.get().papel, 'dp')

        migracao.dp_volta_rh(django_apps, None)
        self.assertEqual(Usuario.objects.get().papel, 'rh')


@override_settings(MEDIA_ROOT=MEDIA_TESTE)
class FichaEPIFluxoTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TESTE, ignore_errors=True)

    def setUp(self):
        self.setor = Setor.objects.create(nome='Produção')
        self.user_seguranca = _criar_usuario('Tecnico SST', '33333333333', 'seguranca_trabalho', self.setor)
        self.user_colaborador = _criar_usuario('Operador', '44444444444', 'colaborador', self.setor, cargo='Operador')
        self.funcionario = Funcionario.objects.get(cpf='44444444444')
        self.botina = ItemCatalogoEPI.objects.create(nome='Botina', numero_ca='12345')
        self.luva = ItemCatalogoEPI.objects.create(nome='Luva', numero_ca='67890')

    def _post_ficha(self, itens):
        dados = {
            'funcionario': self.funcionario.id, 'data_entrega': '2026-10-01',
            'itens-TOTAL_FORMS': str(len(itens)), 'itens-INITIAL_FORMS': '0',
            'itens-MIN_NUM_FORMS': '0', 'itens-MAX_NUM_FORMS': '1000',
        }
        for i, (item, quantidade, vencimento) in enumerate(itens):
            dados[f'itens-{i}-item_catalogo'] = item.id if item else ''
            dados[f'itens-{i}-quantidade'] = quantidade
            dados[f'itens-{i}-data_vencimento'] = vencimento
        return self.client.post(reverse('criar_ficha_epi'), dados)

    def test_cria_ficha_com_itens(self):
        self.client.force_login(self.user_seguranca)
        response = self._post_ficha([(self.botina, 1, '2027-04-01'), (self.luva, 2, '')])

        self.assertRedirects(response, reverse('listar_fichas_epi'), fetch_redirect_response=False)
        ficha = FichaEPI.objects.get()
        self.assertEqual(ficha.gerado_por, self.user_seguranca)
        self.assertEqual(ficha.itens.count(), 2)
        self.assertEqual(ficha.itens.get(item_catalogo=self.botina).data_vencimento, date(2027, 4, 1))

    def test_ficha_criada_ja_aparece_para_o_colaborador_assinar(self):
        self.client.force_login(self.user_seguranca)
        self._post_ficha([(self.botina, 1, '')])
        ficha = FichaEPI.objects.get()
        self.assertTrue(ficha.arquivo_gerado)
        self.assertEqual(ficha.status, 'aguardando_assinatura')

        self.client.force_login(self.user_colaborador)
        meus = self.client.get(reverse('meus_documentos'))
        self.assertContains(meus, reverse('reenviar_epi_assinado', args=[ficha.id]))

    def test_ficha_sem_itens_e_recusada(self):
        self.client.force_login(self.user_seguranca)
        response = self._post_ficha([(None, 1, '')])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(FichaEPI.objects.exists())

    def test_matriz_do_setor_vai_para_a_tela_de_criacao(self):
        MatrizEpiSetor.objects.create(setor=self.setor, item_catalogo=self.botina, quantidade_padrao=2)
        self.client.force_login(self.user_seguranca)
        response = self.client.get(reverse('criar_ficha_epi'))
        self.assertEqual(response.context['matriz_por_setor'], {str(self.setor.id): [{'item': str(self.botina.id), 'quantidade': 2}]})
        busca = {f['id']: f for f in response.context['funcionarios_busca']}
        self.assertEqual(busca[self.funcionario.id]['setor_id'], self.setor.id)

    def test_pdf_traz_o_ca(self):
        ficha = FichaEPI.objects.create(funcionario=self.funcionario, data_entrega=date(2026, 10, 1))
        ItemEPI.objects.create(ficha=ficha, item_catalogo=self.botina)
        self.client.force_login(self.user_seguranca)

        self.client.get(reverse('gerar_pdf_ficha_epi', args=[ficha.id]))

        ficha.refresh_from_db()
        self.assertTrue(ficha.arquivo_gerado)
        self.assertEqual(ficha.status, 'aguardando_assinatura')

    def test_colaborador_baixa_e_reenvia_assinado_e_seguranca_valida(self):
        ficha = FichaEPI.objects.create(funcionario=self.funcionario, data_entrega=date(2026, 10, 1))
        ficha.arquivo_gerado.save('ficha.pdf', ContentFile(b'%PDF-1.4'))

        self.client.force_login(self.user_colaborador)
        meus = self.client.get(reverse('meus_documentos'))
        self.assertContains(meus, reverse('reenviar_epi_assinado', args=[ficha.id]))
        self.assertEqual(self.client.get(reverse('ver_ficha_epi', args=[ficha.id, 'gerado'])).status_code, 200)

        self.client.post(
            reverse('reenviar_epi_assinado', args=[ficha.id]),
            {'arquivo_assinado': SimpleUploadedFile('assinado.pdf', b'%PDF-1.4', content_type='application/pdf')},
        )
        ficha.refresh_from_db()
        self.assertEqual(ficha.status, 'em_validacao')

        self.client.force_login(self.user_seguranca)
        self.client.post(reverse('aprovar_ficha_epi', args=[ficha.id]))
        ficha.refresh_from_db()
        self.assertEqual(ficha.status, 'ativo')
        self.assertEqual(ficha.validado_por, self.user_seguranca)

    def test_rejeicao_volta_para_assinatura_com_motivo(self):
        ficha = FichaEPI.objects.create(
            funcionario=self.funcionario, data_entrega=date(2026, 10, 1), status='em_validacao',
        )
        self.client.force_login(self.user_seguranca)
        self.client.post(reverse('rejeitar_ficha_epi', args=[ficha.id]), {'motivo': 'Sem assinatura'})
        ficha.refresh_from_db()
        self.assertEqual(ficha.status, 'aguardando_assinatura')
        self.assertEqual(ficha.motivo_rejeicao, 'Sem assinatura')

    def test_colaborador_nao_reenvia_ficha_de_outro(self):
        outro = Funcionario.objects.create(nome='Outro', cpf='77777777777', cargo='X', setor=self.setor)
        ficha = FichaEPI.objects.create(funcionario=outro, data_entrega=date(2026, 10, 1))
        ficha.arquivo_gerado.save('ficha.pdf', ContentFile(b'%PDF-1.4'))
        self.client.force_login(self.user_colaborador)

        response = self.client.post(
            reverse('reenviar_epi_assinado', args=[ficha.id]),
            {'arquivo_assinado': SimpleUploadedFile('a.pdf', b'x', content_type='application/pdf')},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(reverse('ver_ficha_epi', args=[ficha.id, 'gerado'])).status_code, 302)

    def test_epis_vencendo_lista_vencidos_e_proximos(self):
        hoje = timezone.now().date()
        ficha = FichaEPI.objects.create(funcionario=self.funcionario, data_entrega=hoje)
        vencido = ItemEPI.objects.create(ficha=ficha, item_catalogo=self.botina, data_vencimento=hoje - timedelta(days=3))
        proximo = ItemEPI.objects.create(ficha=ficha, item_catalogo=self.luva, data_vencimento=hoje + timedelta(days=10))
        ItemEPI.objects.create(ficha=ficha, item_catalogo=self.luva, data_vencimento=hoje + timedelta(days=90))
        ItemEPI.objects.create(ficha=ficha, item_catalogo=self.luva, data_vencimento=hoje, devolvido=True)
        self.client.force_login(self.user_seguranca)

        response = self.client.get(reverse('listar_epis_vencendo'))

        self.assertEqual(list(response.context['itens']), [vencido, proximo])
        self.assertContains(response, 'Vencido há 3 dias')

    def test_fichas_e_vencendo_sao_abas_da_mesma_tela(self):
        hoje = timezone.now().date()
        ficha = FichaEPI.objects.create(funcionario=self.funcionario, data_entrega=hoje)
        ItemEPI.objects.create(ficha=ficha, item_catalogo=self.botina, data_vencimento=hoje - timedelta(days=1))
        self.client.force_login(self.user_seguranca)

        for url in (reverse('listar_fichas_epi'), reverse('listar_epis_vencendo')):
            response = self.client.get(url)
            self.assertContains(response, 'class="tabs"')
            self.assertContains(response, f'href="{reverse("listar_epis_vencendo")}"')
            self.assertContains(response, f'href="{reverse("listar_fichas_epi")}"')
            self.assertEqual(response.context['total_pendencias_epi'], 1)
            # O alerta de troca virou aba: não tem mais item próprio na sidebar.
            self.assertNotContains(response, 'EPIs Vencendo')

    def _desligar_com_epi(self):
        hoje = timezone.now().date()
        ficha = FichaEPI.objects.create(funcionario=self.funcionario, data_entrega=hoje, status='ativo')
        botina = ItemEPI.objects.create(ficha=ficha, item_catalogo=self.botina, data_vencimento=hoje - timedelta(days=1))
        luva = ItemEPI.objects.create(ficha=ficha, item_catalogo=self.luva)
        self.funcionario.status = 'inativo'
        self.funcionario.save()
        return ficha, botina, luva

    def test_desligado_com_epi_notifica_devolucao_e_sai_da_troca(self):
        ficha, botina, _ = self._desligar_com_epi()
        self.client.force_login(self.user_seguranca)

        response = self.client.get(reverse('listar_epis_vencendo'))

        self.assertEqual(response.context['devolucoes'], [ficha])
        self.assertEqual(response.context['itens'], [])  # desligado devolve, não troca
        self.assertContains(response, reverse('registrar_devolucao_epi', args=[ficha.id]))
        self.assertEqual(response.context['badge_devolucoes_epi'], 1)
        # Sem ficha para validar, o atalho da sidebar leva direto às devoluções.
        self.assertContains(response, f'href="{reverse("listar_epis_vencendo")}">')

    def test_devolucao_parcial_e_depois_total(self):
        ficha, botina, luva = self._desligar_com_epi()
        self.client.force_login(self.user_seguranca)
        url = reverse('registrar_devolucao_epi', args=[ficha.id])

        self.client.post(url, {'itens': [botina.id], 'observacoes': 'Botina gasta'})
        ficha.refresh_from_db()
        self.assertEqual(ficha.status, 'devolvido_parcial')
        self.assertEqual(ficha.observacoes_devolucao, 'Botina gasta')
        self.assertEqual(list(FichaEPI.com_devolucao_pendente()), [ficha])

        self.client.post(url, {'itens': [luva.id]})
        ficha.refresh_from_db()
        self.assertEqual(ficha.status, 'devolvido_total')
        self.assertEqual(ficha.data_devolucao, timezone.now().date())
        self.assertFalse(FichaEPI.com_devolucao_pendente().exists())

    def test_devolucao_sem_item_marcado_nao_altera_a_ficha(self):
        ficha, _, _ = self._desligar_com_epi()
        self.client.force_login(self.user_seguranca)
        self.client.post(reverse('registrar_devolucao_epi', args=[ficha.id]), {})
        ficha.refresh_from_db()
        self.assertEqual(ficha.status, 'ativo')

    def test_colaborador_nao_registra_devolucao(self):
        ficha, botina, _ = self._desligar_com_epi()
        self.client.force_login(self.user_colaborador)
        self.client.post(reverse('registrar_devolucao_epi', args=[ficha.id]), {'itens': [botina.id]})
        botina.refresh_from_db()
        self.assertFalse(botina.devolvido)

    def test_matriz_salva_e_remove_itens_do_setor(self):
        self.client.force_login(self.user_seguranca)
        url = reverse('gerenciar_matriz_epi')

        self.client.post(url, {'setor': self.setor.id, f'item_{self.botina.id}': '1', f'quantidade_{self.botina.id}': '2'})
        self.assertEqual(list(MatrizEpiSetor.objects.values_list('item_catalogo', 'quantidade_padrao')), [(self.botina.id, 2)])

        self.client.post(url, {'setor': self.setor.id, f'item_{self.luva.id}': '1'})
        self.assertEqual(list(MatrizEpiSetor.objects.values_list('item_catalogo', flat=True)), [self.luva.id])

        response = self.client.get(url, {'setor': self.setor.id})
        self.assertContains(response, 'EPIs obrigatórios — Produção')

    def test_matriz_recusa_setor_que_nao_existe(self):
        self.client.force_login(self.user_seguranca)
        for setor in ('99999', 'abc'):
            self.client.post(reverse('gerenciar_matriz_epi'), {'setor': setor, f'item_{self.botina.id}': '1'})
        self.assertFalse(MatrizEpiSetor.objects.exists())

    def test_registra_e_filtra_ocorrencia(self):
        self.client.force_login(self.user_seguranca)
        self.client.post(reverse('criar_ocorrencia_epi'), {
            'funcionario': self.funcionario.id, 'data_ocorrencia': '2026-10-01',
            'descricao': 'Sem óculos na linha.', 'item_catalogo': '',
        })
        ocorrencia = OcorrenciaEPI.objects.get()
        self.assertEqual(ocorrencia.tecnico, self.user_seguranca)

        url = reverse('listar_ocorrencias_epi')
        self.assertEqual(len(self.client.get(url, {'de': '2026-10-01'}).context['ocorrencias']), 1)
        self.assertEqual(len(self.client.get(url, {'de': '2026-10-02'}).context['ocorrencias']), 0)
        self.assertEqual(self.client.get(url, {'de': 'lixo'}).status_code, 200)
