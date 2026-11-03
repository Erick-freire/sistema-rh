import threading
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.management import call_command
from django.db import connection
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from funcionarios.models import Atestado, Funcionario, Setor, SolicitacaoFerias, Usuario
from .calculos import calcular_periodo, intervalo_curto, intervalo_minimo, situacao_do_dia, valores_do_periodo
from .controlid_client import ControlIdAPIError, ControlIdTimeoutError, ControlIdUsuarioNaoEncontradoError
from .models import (
    AjustePonto, Equipamento, Feriado, FuncionarioJornada, Horario, HorarioDia,
    MarcacaoRelogio, SincronizacaoFuncionario,
)
from .services import (
    _hash_dados_funcionario,
    coletar_marcacoes,
    inicio_da_revinculacao,
    processar_pendencias,
    resumo_marcacoes_sem_funcionario,
    sincronizar_funcionario_no_equipamento,
)


def _criar_funcionario(**kwargs):
    setor = Setor.objects.create(nome='TI')
    defaults = dict(nome='Ana Silva', matricula='1001', cargo='Analista', setor=setor, cpf='11111111111')
    defaults.update(kwargs)
    return Funcionario.objects.create(**defaults)


def _criar_equipamento(**kwargs):
    defaults = dict(nome='Relógio Portaria', ip='192.168.1.50', porta=443, usuario_admin='admin', senha_admin='admin')
    defaults.update(kwargs)
    return Equipamento.objects.create(**defaults)


class SincronizacaoAutomaticaTests(TestCase):
    """Item 3: cadastrar funcionário dispara sincronização, sem duplicar cadastro no RH."""

    def test_criar_funcionario_enfileira_sincronizacao_para_equipamentos_ativos(self):
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario()

        sincronizacoes = SincronizacaoFuncionario.objects.filter(funcionario=funcionario)
        self.assertEqual(sincronizacoes.count(), 1)
        self.assertEqual(sincronizacoes.first().equipamento, equipamento)
        self.assertEqual(sincronizacoes.first().status, 'pendente')

    def test_equipamento_inativo_nao_recebe_sincronizacao(self):
        _criar_equipamento(ativo=False)
        funcionario = _criar_funcionario()

        self.assertEqual(SincronizacaoFuncionario.objects.filter(funcionario=funcionario).count(), 0)

    def test_salvar_campo_nao_relevante_nao_reenfileira(self):
        _criar_equipamento()
        funcionario = _criar_funcionario()
        sincronizacao = SincronizacaoFuncionario.objects.get(funcionario=funcionario)
        sincronizacao.status = 'sincronizado'
        sincronizacao.hash_dados_sincronizados = _hash_dados_funcionario(funcionario)
        sincronizacao.save()

        funcionario.endereco = 'Rua Nova, 123'
        funcionario.save()

        sincronizacao.refresh_from_db()
        self.assertEqual(sincronizacao.status, 'sincronizado')

    def test_mudar_nome_reenfileira_sincronizacao(self):
        _criar_equipamento()
        funcionario = _criar_funcionario()
        sincronizacao = SincronizacaoFuncionario.objects.get(funcionario=funcionario)
        sincronizacao.status = 'sincronizado'
        sincronizacao.save()

        funcionario.nome = 'Ana Silva Souza'
        funcionario.save()

        sincronizacao.refresh_from_db()
        self.assertEqual(sincronizacao.status, 'pendente')


class CadastroNoEquipamentoTests(TestCase):
    """Itens 3, 4 e 5: cadastro no relógio, robustez a relógio offline, sem duplicar."""

    @patch('ponto.services.client.buscar_usuario_por_cpf', return_value={'templates_count': 0})
    @patch('ponto.services.client.update_user', side_effect=ControlIdUsuarioNaoEncontradoError('Usuário não encontrado'))
    @patch('ponto.services.client.add_user')
    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_cadastro_com_relogio_online(self, mock_login, mock_add, mock_update, mock_buscar):
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario()
        sincronizacao = SincronizacaoFuncionario.objects.get(funcionario=funcionario, equipamento=equipamento)

        ok = sincronizar_funcionario_no_equipamento(sincronizacao)

        self.assertTrue(ok)
        sincronizacao.refresh_from_db()
        self.assertEqual(sincronizacao.status, 'aguardando_biometria')
        self.assertEqual(sincronizacao.id_usuario_dispositivo, funcionario.cpf)
        mock_add.assert_called_once_with(equipamento, 'sessao-fake', funcionario.cpf, funcionario.nome)

    @patch('ponto.services.client.buscar_usuario_por_cpf', return_value={'templates_count': 3})
    @patch('ponto.services.client.update_user')
    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_cadastro_com_biometria_ja_cadastrada_marca_sincronizado(self, mock_login, mock_update, mock_buscar):
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario()
        sincronizacao = SincronizacaoFuncionario.objects.get(funcionario=funcionario, equipamento=equipamento)

        sincronizar_funcionario_no_equipamento(sincronizacao)

        sincronizacao.refresh_from_db()
        self.assertEqual(sincronizacao.status, 'sincronizado')

    @patch('ponto.services.client.login', side_effect=ControlIdTimeoutError('timeout'))
    def test_cadastro_com_relogio_offline_nao_impede_funcionario_existir(self, mock_login):
        equipamento = _criar_equipamento()
        # Funcionario.save() (via signal) nunca deve levantar exceção mesmo com relógio offline.
        funcionario = _criar_funcionario()

        self.assertTrue(Funcionario.objects.filter(id=funcionario.id).exists())

        sincronizacao = SincronizacaoFuncionario.objects.get(funcionario=funcionario, equipamento=equipamento)
        ok = sincronizar_funcionario_no_equipamento(sincronizacao)

        self.assertFalse(ok)
        sincronizacao.refresh_from_db()
        self.assertEqual(sincronizacao.status, 'erro')
        self.assertEqual(sincronizacao.tentativas, 1)
        equipamento.refresh_from_db()
        self.assertEqual(equipamento.status_conexao, 'offline')

    @patch('ponto.services.client.buscar_usuario_por_cpf', return_value={'templates_count': 0})
    @patch('ponto.services.client.update_user', side_effect=ControlIdUsuarioNaoEncontradoError('Usuário não encontrado'))
    @patch('ponto.services.client.add_user')
    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_relogio_volta_e_retry_resolve_pendencia(self, mock_login, mock_add, mock_update, mock_buscar):
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario()
        sincronizacao = SincronizacaoFuncionario.objects.get(funcionario=funcionario, equipamento=equipamento)
        sincronizacao.status = 'erro'
        sincronizacao.tentativas = 3
        sincronizacao.save()

        processadas, sucesso = processar_pendencias()

        self.assertEqual(processadas, 1)
        self.assertEqual(sucesso, 1)
        sincronizacao.refresh_from_db()
        self.assertEqual(sincronizacao.status, 'aguardando_biometria')

    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_erro_que_esgotou_tentativas_para_de_ser_retentado_sozinho(self, mock_login):
        """
        Erro de formato (ex.: equipamento cadastrado por PIS, funcionário só
        tem CPF) nunca vai se resolver sozinho de tentativa em tentativa --
        sem esse limite, toda sincronização perde tempo tentando de novo
        pendências que já provaram que não vão vingar.
        """
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario()
        sincronizacao = SincronizacaoFuncionario.objects.get(funcionario=funcionario, equipamento=equipamento)
        sincronizacao.status = 'erro'
        sincronizacao.tentativas = 5
        sincronizacao.save()

        processadas, sucesso = processar_pendencias()

        self.assertEqual(processadas, 0)
        mock_login.assert_not_called()
        sincronizacao.refresh_from_db()
        self.assertEqual(sincronizacao.tentativas, 5)

    @patch('ponto.services.client.buscar_usuario_por_cpf', return_value={'templates_count': 0})
    @patch('ponto.services.client.add_user')
    @patch('ponto.services.client.update_user')  # não levanta exceção: CPF já existe no equipamento
    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_nao_duplica_cadastro_se_usuario_ja_existe_no_equipamento(self, mock_login, mock_update, mock_add, mock_buscar):
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario()
        sincronizacao = SincronizacaoFuncionario.objects.get(funcionario=funcionario, equipamento=equipamento)

        # Como o vínculo é sempre pelo CPF (não por um id gerado pelo equipamento),
        # basta chamar update_user primeiro: se o CPF já existe lá, nunca chamamos add_user.
        sincronizar_funcionario_no_equipamento(sincronizacao)

        mock_update.assert_called_once_with(equipamento, 'sessao-fake', funcionario.cpf, funcionario.nome)
        mock_add.assert_not_called()
        sincronizacao.refresh_from_db()
        self.assertEqual(sincronizacao.id_usuario_dispositivo, funcionario.cpf)

    @patch('ponto.services.client.buscar_usuario_por_cpf', return_value={'templates_count': 2})
    @patch('ponto.services.client.update_user')
    @patch('ponto.services.client.add_user')
    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_atualiza_pelo_identificador_ja_conhecido_no_equipamento_nao_pelo_cpf(
        self, mock_login, mock_add, mock_update, mock_buscar,
    ):
        """
        Gente cadastrada no equipamento pelo Secullum antes deste sistema
        existir pode estar lá pelo PIS, não pelo CPF (comum no relógio ADM).
        Uma vez que sabemos disso (id_usuario_dispositivo preenchido por uma
        reconciliação), a atualização tem que usar esse identificador -- usar
        o CPF aqui devolveria "não encontrado" e duplicaria o cadastro sem a
        biometria já capturada.
        """
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario()
        pis = '12345678901'
        sincronizacao = SincronizacaoFuncionario.objects.get(funcionario=funcionario, equipamento=equipamento)
        sincronizacao.id_usuario_dispositivo = pis
        sincronizacao.status = 'sincronizado'
        sincronizacao.save()

        sincronizar_funcionario_no_equipamento(sincronizacao)

        mock_update.assert_called_once_with(equipamento, 'sessao-fake', pis, funcionario.nome)
        mock_add.assert_not_called()
        sincronizacao.refresh_from_db()
        self.assertEqual(sincronizacao.id_usuario_dispositivo, pis)  # continua o mesmo, não vira o CPF

    @patch('ponto.services.client.buscar_usuario_por_cpf', return_value={'templates_count': 0})
    @patch('ponto.services.client.update_user', side_effect=ControlIdUsuarioNaoEncontradoError('Usuário não encontrado'))
    @patch('ponto.services.client.add_user')
    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_identificador_desatualizado_cai_para_cadastro_novo_pelo_cpf(
        self, mock_login, mock_add, mock_update, mock_buscar,
    ):
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario()
        sincronizacao = SincronizacaoFuncionario.objects.get(funcionario=funcionario, equipamento=equipamento)
        sincronizacao.id_usuario_dispositivo = '00000000000'  # identificador errado/obsoleto
        sincronizacao.save()

        sincronizar_funcionario_no_equipamento(sincronizacao)

        mock_update.assert_called_once_with(equipamento, 'sessao-fake', '00000000000', funcionario.nome)
        mock_add.assert_called_once_with(equipamento, 'sessao-fake', funcionario.cpf, funcionario.nome)
        sincronizacao.refresh_from_db()
        self.assertEqual(sincronizacao.id_usuario_dispositivo, funcionario.cpf)

    @patch('ponto.services.client.remove_user')
    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_funcionario_inativado_desativa_no_equipamento_sem_apagar_historico(self, mock_login, mock_remove):
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario()
        MarcacaoRelogio.objects.create(
            equipamento=equipamento,
            funcionario=funcionario,
            id_evento_dispositivo='1',
            momento='2026-08-01T08:00:00Z',
        )

        funcionario.status = 'inativo'
        funcionario.save()

        sincronizacao = SincronizacaoFuncionario.objects.get(funcionario=funcionario, equipamento=equipamento)
        self.assertEqual(sincronizacao.acao_pendente, 'desativar')

        sincronizacao.id_usuario_dispositivo = funcionario.cpf
        sincronizacao.save()
        sincronizar_funcionario_no_equipamento(sincronizacao)

        mock_remove.assert_called_once_with(equipamento, 'sessao-fake', funcionario.cpf)
        self.assertEqual(MarcacaoRelogio.objects.filter(funcionario=funcionario).count(), 1)


class ColetaMarcacoesTests(TestCase):
    """Itens 7, 8 e 9: coleta em background, imutabilidade e idempotência."""

    def _linha_afd(self, nsr, cpf, momento_iso, prefixo='9'):
        # Formato oficial (leiaute Portaria 1510/671), tipo "3" = marcação de
        # ponto: NSR(9) + TIPO(1)='3' + DATA_ISO(24) + PREFIXO(1) + CPF(11) + CRC(4).
        return f'{nsr:09d}3{momento_iso}{prefixo}{cpf}abcd'

    def _texto_afd(self, registros):
        linhas = [self._linha_afd(nsr, cpf, momento) for nsr, cpf, momento in registros]
        linhas.append('AFD0001400375039878904577125000130REP_C.txt')  # rodapé, deve ser ignorado
        return '\n'.join(linhas)

    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_coleta_nao_duplica_ao_rodar_duas_vezes(self, mock_login):
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario(cpf='12345678901')
        texto = self._texto_afd([
            (1, '12345678901', '2026-08-16T08:00:00-0300'),
            (2, '12345678901', '2026-08-16T12:00:00-0300'),
        ])

        with patch('ponto.services.client.get_afd', return_value=texto):
            primeira = coletar_marcacoes(equipamento)
            segunda = coletar_marcacoes(equipamento)

        self.assertEqual(primeira, 2)
        self.assertEqual(segunda, 0)
        self.assertEqual(MarcacaoRelogio.objects.filter(equipamento=equipamento).count(), 2)
        self.assertEqual(
            MarcacaoRelogio.objects.filter(equipamento=equipamento, funcionario=funcionario).count(), 2
        )
        equipamento.refresh_from_db()
        self.assertEqual(equipamento.ultimo_nsr_coletado, 2)

    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_coleta_em_lotes_processa_tudo_e_avanca_checkpoint_pro_maior_nsr(self, mock_login):
        """
        Regressão do travamento da Farinha: get_afd devolve tudo de uma vez
        (não pagina), então processamos em lotes salvando o checkpoint a cada
        um -- se parar no meio, o que já rodou fica salvo. Aqui simulamos com
        um lote bem menor que o total pra garantir que múltiplos lotes somam
        certo e o checkpoint final é o maior NSR de todos, não só do último lote.
        """
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario(cpf='12345678901')
        texto = self._texto_afd([
            (n, '12345678901', f'2026-08-{n:02d}T08:00:00-0300') for n in range(1, 6)
        ])

        with patch('ponto.services.client.get_afd', return_value=texto), \
             patch('ponto.services.TAMANHO_LOTE_COLETA', 2):
            total = coletar_marcacoes(equipamento)

        self.assertEqual(total, 5)
        self.assertEqual(MarcacaoRelogio.objects.filter(equipamento=equipamento).count(), 5)
        equipamento.refresh_from_db()
        self.assertEqual(equipamento.ultimo_nsr_coletado, 5)

    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_coleta_usa_o_identificador_que_o_relogio_usa_pra_pessoa(self, mock_login):
        # Caso real (matrícula 1225): no ADM o Secullum cadastrou um "PIS" de 12
        # dígitos (870819163173), diferente do CPF (70819163171). A reconciliação
        # já sabe disso; a coleta tem que usar.
        adm = _criar_equipamento(nome='PONTO ADM', ip='192.168.1.71')
        outro = _criar_equipamento(nome='PONTO FARINHA', ip='192.168.1.72')
        funcionario = _criar_funcionario(cpf='70819163171')
        SincronizacaoFuncionario.objects.update_or_create(
            funcionario=funcionario, equipamento=adm,
            defaults={'id_usuario_dispositivo': '870819163173', 'status': 'sincronizado'},
        )
        linha = self._linha_afd(1, '70819163173', '2026-10-09T07:16:00-0300', prefixo='8')

        with patch('ponto.services.client.get_afd', return_value=linha):
            coletar_marcacoes(adm)
            coletar_marcacoes(outro)  # o vínculo é só do ADM

        self.assertEqual(MarcacaoRelogio.objects.get(equipamento=adm).funcionario, funcionario)
        self.assertIsNone(MarcacaoRelogio.objects.get(equipamento=outro).funcionario)

    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_cpf_com_zero_na_frente_casa_com_o_cadastro(self, mock_login):
        # O AFD real preenche as 12 posições com um 0 antes do CPF.
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario(cpf='04268100156')
        linha = self._linha_afd(1, '04268100156', '2026-10-09T07:16:00-0300', prefixo='0')

        with patch('ponto.services.client.get_afd', return_value=linha):
            coletar_marcacoes(equipamento)

        self.assertEqual(MarcacaoRelogio.objects.get(equipamento=equipamento).funcionario, funcionario)

    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_coleta_vincula_marcacao_recente_que_ficou_sem_dono(self, mock_login):
        # Caso real (matrícula 1473, 22/10/2026): batidas gravadas sem dono não
        # voltavam a ser olhadas -- coletar de novo não mudava nada, porque a
        # coleta só pede NSR novo e não mexe no que já existe.
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario(cpf='70819163171')
        SincronizacaoFuncionario.objects.update_or_create(
            funcionario=funcionario, equipamento=equipamento,
            defaults={'id_usuario_dispositivo': '870819163173', 'status': 'sincronizado'},
        )
        dados = {'prefixo': '8', 'identificador': '70819163173'}
        recente = MarcacaoRelogio.objects.create(
            equipamento=equipamento, funcionario=None, id_evento_dispositivo='1',
            momento=timezone.now() - timedelta(hours=1), dados_brutos=dados,
        )
        antiga = MarcacaoRelogio.objects.create(
            equipamento=equipamento, funcionario=None, id_evento_dispositivo='2',
            momento=inicio_da_revinculacao() - timedelta(minutes=1), dados_brutos=dados,
        )

        with patch('ponto.services.client.get_afd', return_value=''):  # nada novo no relógio
            coletar_marcacoes(equipamento)

        recente.refresh_from_db(); antiga.refresh_from_db()
        self.assertEqual(recente.funcionario, funcionario)
        self.assertIsNone(antiga.funcionario)  # fora da janela: só pelo comando

    @patch('ponto.services.client.login', side_effect=ControlIdAPIError('equipamento indisponível'))
    def test_relogio_offline_ainda_vincula_marcacao_sem_dono(self, mock_login):
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario(cpf='12345678901')
        marcacao = MarcacaoRelogio.objects.create(
            equipamento=equipamento, funcionario=None, id_evento_dispositivo='1',
            momento=timezone.now(), dados_brutos={'prefixo': '0', 'identificador': '12345678901'},
        )

        coletar_marcacoes(equipamento)

        marcacao.refresh_from_db()
        self.assertEqual(marcacao.funcionario, funcionario)

    @patch('ponto.services.client.login', side_effect=ControlIdAPIError('equipamento indisponível'))
    def test_relogio_offline_durante_coleta_nao_derruba_e_nao_perde_checkpoint(self, mock_login):
        equipamento = _criar_equipamento(ultimo_nsr_coletado=10)

        coletadas = coletar_marcacoes(equipamento)

        self.assertEqual(coletadas, 0)
        equipamento.refresh_from_db()
        self.assertEqual(equipamento.ultimo_nsr_coletado, 10)
        self.assertEqual(equipamento.status_conexao, 'offline')

    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_rodape_do_afd_nao_vira_marcacao(self, mock_login):
        equipamento = _criar_equipamento()
        texto = self._texto_afd([(1, '12345678901', '2026-08-16T08:00:00-0300')])

        with patch('ponto.services.client.get_afd', return_value=texto):
            coletar_marcacoes(equipamento)

        self.assertEqual(MarcacaoRelogio.objects.filter(equipamento=equipamento).count(), 1)

    def test_marcacao_nao_expoe_edicao_via_model(self):
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario()
        marcacao = MarcacaoRelogio.objects.create(
            equipamento=equipamento, funcionario=funcionario,
            id_evento_dispositivo='1', momento='2026-08-16T08:00:00Z',
        )
        # Não há view nem form que permita editar/excluir MarcacaoRelogio -- só existe leitura.
        self.assertNotIn('editar_marcacao', str(marcacao._meta.model_name))


class ConcorrenciaSincronizacaoTests(TransactionTestCase):
    """Item 10: dois processos tentando sincronizar a mesma pendência ao mesmo tempo."""

    @patch('ponto.services.client.buscar_usuario_por_cpf', return_value={'templates_count': 0})
    @patch('ponto.services.client.add_user')
    @patch('ponto.services.client.update_user', side_effect=ControlIdUsuarioNaoEncontradoError('Usuário não encontrado'))
    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_dois_processos_concorrentes_nao_duplicam_cadastro(self, mock_login, mock_update, mock_add, mock_buscar):
        # Os mocks são instalados uma única vez para as duas threads: fazer o patch
        # dentro de cada thread corromperia um ao outro (unittest.mock.patch não é
        # pensado para instalação/remoção concorrente do mesmo alvo).
        equipamento = _criar_equipamento()
        _criar_funcionario()

        def rodar():
            processar_pendencias()
            connection.close()

        t1 = threading.Thread(target=rodar)
        t2 = threading.Thread(target=rodar)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        mock_add.assert_called_once()
        self.assertEqual(SincronizacaoFuncionario.objects.filter(status='aguardando_biometria').count(), 1)


class EquipamentoViewsTests(TestCase):
    """Botões de desativar/ativar e excluir na tela de Equipamentos."""

    def setUp(self):
        setor = Setor.objects.create(nome='RH')
        funcionario = Funcionario.objects.create(
            nome='Rh Teste', matricula='9999', cargo='RH', setor=setor, cpf='22222222222',
        )
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=funcionario, papel='rh')
        self.client.login(username='rhteste', password='senha123')

    def test_lista_quem_bate_ponto_sem_cadastro(self):
        equipamento = _criar_equipamento()
        MarcacaoRelogio.objects.create(
            equipamento=equipamento, funcionario=None, id_evento_dispositivo='1', momento=timezone.now(),
            dados_brutos={'prefixo': '0', 'identificador': '23776484426'},
        )

        resposta = self.client.get(reverse('ponto:listar_equipamentos'))

        self.assertContains(resposta, 'Batidas sem funcionário')
        self.assertContains(resposta, '23776484426')

    def test_sem_batidas_sem_dono_nao_mostra_o_quadro(self):
        _criar_equipamento()

        resposta = self.client.get(reverse('ponto:listar_equipamentos'))

        self.assertNotContains(resposta, 'Batidas sem funcionário')

    def test_alternar_status_desativa_e_reativa(self):
        equipamento = _criar_equipamento()
        self.assertTrue(equipamento.ativo)

        self.client.post(reverse('ponto:alternar_status_equipamento', args=[equipamento.id]))
        equipamento.refresh_from_db()
        self.assertFalse(equipamento.ativo)

        self.client.post(reverse('ponto:alternar_status_equipamento', args=[equipamento.id]))
        equipamento.refresh_from_db()
        self.assertTrue(equipamento.ativo)

    def test_excluir_equipamento_sem_marcacoes_funciona(self):
        equipamento = _criar_equipamento()

        self.client.post(reverse('ponto:excluir_equipamento', args=[equipamento.id]))

        self.assertFalse(Equipamento.objects.filter(id=equipamento.id).exists())

    def test_excluir_equipamento_com_marcacoes_e_bloqueado_sem_quebrar(self):
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario()
        MarcacaoRelogio.objects.create(
            equipamento=equipamento, funcionario=funcionario,
            id_evento_dispositivo='1', momento='2026-08-16T08:00:00Z',
        )

        resposta = self.client.post(reverse('ponto:excluir_equipamento', args=[equipamento.id]), follow=True)

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(Equipamento.objects.filter(id=equipamento.id).exists())
        self.assertEqual(MarcacaoRelogio.objects.filter(equipamento=equipamento).count(), 1)


class CadastroFuncionarioComJornadaTests(TestCase):
    """Campo de horário na tela de pré-cadastro (funcionarios/novo/)."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='RH')
        funcionario_rh = Funcionario.objects.create(
            nome='Rh Teste', matricula='9999', cargo='RH', setor=self.setor, cpf='22222222222',
        )
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=funcionario_rh, papel='rh')
        self.client.login(username='rhteste', password='senha123')

    def _dados_basicos(self, cpf):
        return dict(
            nome='Novo Funcionario', cpf=cpf, pis='12345678901', cargo='Analista', setor=self.setor.id,
            data_admissao='2026-08-17',  # dia ímpar
        )

    def test_cadastro_com_horario_cria_jornada_a_partir_da_admissao(self):
        horario = _criar_horario_util(5001)
        dados = self._dados_basicos('33333333333')
        dados['horario'] = horario.id

        self.client.post(reverse('cadastrar_funcionario'), dados)

        funcionario = Funcionario.objects.get(cpf='33333333333')
        jornada = FuncionarioJornada.objects.get(funcionario=funcionario)
        self.assertEqual(jornada.horario, horario)
        self.assertEqual(jornada.data_inicio, date(2026, 8, 17))

    def test_cadastro_com_horario_12x36_usa_admissao_como_dia_de_trabalho(self):
        horario = _criar_horario_12x36(5002)
        dados = self._dados_basicos('44444444445')
        dados['horario'] = horario.id

        self.client.post(reverse('cadastrar_funcionario'), dados)

        funcionario = Funcionario.objects.get(cpf='44444444445')
        jornada = FuncionarioJornada.objects.get(funcionario=funcionario)
        self.assertEqual(jornada.data_inicio, date(2026, 8, 17))
        self.assertTrue(jornada.trabalha_no_dia_12x36(date(2026, 8, 17)))

    def test_cadastro_sem_horario_nao_cria_jornada(self):
        dados = self._dados_basicos('44444444444')

        self.client.post(reverse('cadastrar_funcionario'), dados)

        funcionario = Funcionario.objects.get(cpf='44444444444')
        self.assertFalse(FuncionarioJornada.objects.filter(funcionario=funcionario).exists())


class EnviarReceberBotoesTests(TestCase):
    """
    Botões separados "Enviar" (cadastro pendente) e "Receber" (marcações) na
    tela de Equipamentos -- igual ao Enviar/Receber do Secullum, pra quem só
    quer as marcações não precisar esperar o envio de cadastro (que pode
    demorar bem mais quando tem muita pendência acumulada).
    """

    def setUp(self):
        setor = Setor.objects.create(nome='RH')
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9999', cargo='RH', setor=setor, cpf='22222222222')
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.login(username='rhteste', password='senha123')

    def _linha_afd(self, nsr, cpf, momento_iso):
        return f'{nsr:09d}3{momento_iso}9{cpf}abcd'

    @patch('ponto.services.client.buscar_usuario_por_cpf', return_value={'templates_count': 0})
    @patch('ponto.services.client.add_user')
    @patch('ponto.services.client.update_user', side_effect=ControlIdUsuarioNaoEncontradoError('Usuário não encontrado'))
    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_enviar_equipamento_resolve_so_daquele_equipamento(self, mock_login, mock_update, mock_add, mock_buscar):
        eq1 = _criar_equipamento(nome='Equip 1', ip='192.168.1.51')
        eq2 = _criar_equipamento(nome='Equip 2', ip='192.168.1.52')
        _criar_funcionario(cpf='33333333333')  # pendente nos dois equipamentos

        resposta = self.client.post(reverse('ponto:enviar_equipamento', args=[eq1.id]))

        self.assertRedirects(resposta, reverse('ponto:listar_equipamentos'))
        self.assertEqual(SincronizacaoFuncionario.objects.get(equipamento=eq1).status, 'aguardando_biometria')
        self.assertEqual(SincronizacaoFuncionario.objects.get(equipamento=eq2).status, 'pendente')

    @patch('ponto.services.client.buscar_usuario_por_cpf', return_value={'templates_count': 0})
    @patch('ponto.services.client.add_user')
    @patch('ponto.services.client.update_user', side_effect=ControlIdUsuarioNaoEncontradoError('Usuário não encontrado'))
    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_enviar_todos_resolve_todos_os_equipamentos(self, mock_login, mock_update, mock_add, mock_buscar):
        _criar_equipamento(nome='Equip 1', ip='192.168.1.51')
        _criar_equipamento(nome='Equip 2', ip='192.168.1.52')
        _criar_funcionario(cpf='33333333333')

        resposta = self.client.post(reverse('ponto:enviar_todos'))

        self.assertRedirects(resposta, reverse('ponto:listar_equipamentos'))
        self.assertEqual(SincronizacaoFuncionario.objects.filter(status='aguardando_biometria').count(), 2)

    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_receber_equipamento_coleta_so_daquele_equipamento(self, mock_login):
        eq1 = _criar_equipamento(nome='Equip 1', ip='192.168.1.51')
        eq2 = _criar_equipamento(nome='Equip 2', ip='192.168.1.52')
        funcionario = _criar_funcionario(cpf='33333333333')
        texto = self._linha_afd(1, '33333333333', '2026-08-16T08:00:00-0300')

        with patch('ponto.services.client.get_afd', return_value=texto):
            resposta = self.client.post(reverse('ponto:receber_equipamento', args=[eq1.id]))

        self.assertRedirects(resposta, reverse('ponto:listar_equipamentos'))
        self.assertEqual(MarcacaoRelogio.objects.filter(equipamento=eq1, funcionario=funcionario).count(), 1)
        self.assertEqual(MarcacaoRelogio.objects.filter(equipamento=eq2).count(), 0)
        # "Receber" não mexe em cadastro pendente -- continua tudo pendente.
        self.assertEqual(SincronizacaoFuncionario.objects.get(equipamento=eq1).status, 'pendente')

    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_receber_todos_coleta_de_todos_os_equipamentos(self, mock_login):
        eq1 = _criar_equipamento(nome='Equip 1', ip='192.168.1.51')
        eq2 = _criar_equipamento(nome='Equip 2', ip='192.168.1.52')
        _criar_funcionario(cpf='33333333333')
        texto = self._linha_afd(1, '33333333333', '2026-08-16T08:00:00-0300')

        with patch('ponto.services.client.get_afd', return_value=texto):
            resposta = self.client.post(reverse('ponto:receber_todos'))

        self.assertRedirects(resposta, reverse('ponto:listar_equipamentos'))
        self.assertEqual(MarcacaoRelogio.objects.filter(equipamento=eq1).count(), 1)
        self.assertEqual(MarcacaoRelogio.objects.filter(equipamento=eq2).count(), 1)

    def test_tela_de_equipamentos_nao_mostra_nome_de_funcionario(self):
        _criar_equipamento()
        _criar_funcionario(nome='Fulano De Tal', cpf='33333333333')

        resposta = self.client.get(reverse('ponto:listar_equipamentos'))

        self.assertEqual(resposta.status_code, 200)
        self.assertNotIn(b'Fulano De Tal', resposta.content)


def _dados_formset_horario(dia_semana_com_entrada1=None, total_forms=7):
    """Monta o POST do management_form + 7 dias (vazios, exceto o indicado)."""
    dados = {
        'dias-TOTAL_FORMS': str(total_forms),
        'dias-INITIAL_FORMS': '0',
        'dias-MIN_NUM_FORMS': '0',
        'dias-MAX_NUM_FORMS': '7',
    }
    for i in range(total_forms):
        dia = i + 1
        dados[f'dias-{i}-dia_semana'] = str(dia)
        for campo in ('entrada1', 'saida1', 'entrada2', 'saida2', 'entrada3', 'saida3'):
            dados[f'dias-{i}-{campo}'] = ''
        if dia == dia_semana_com_entrada1:
            dados[f'dias-{i}-entrada1'] = '08:00'
            dados[f'dias-{i}-saida1'] = '17:00'
    return dados


class HorarioViewsTests(TestCase):
    """Módulo de Horários: cadastro/edição da jornada por dia da semana."""

    def setUp(self):
        setor = Setor.objects.create(nome='RH')
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9999', cargo='RH', setor=setor, cpf='22222222222')
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.login(username='rhteste', password='senha123')

    def test_cadastrar_horario_com_dias_da_semana(self):
        dados = {'numero': '2001', 'nome': 'Turno Teste'}
        dados.update(_dados_formset_horario(dia_semana_com_entrada1=1))

        resposta = self.client.post(reverse('ponto:cadastrar_horario'), dados)

        self.assertRedirects(resposta, reverse('ponto:listar_horarios'))
        horario = Horario.objects.get(numero=2001)
        self.assertEqual(horario.dias.count(), 7)
        segunda = horario.dias.get(dia_semana=1)
        self.assertEqual(str(segunda.entrada1), '08:00:00')
        terca = horario.dias.get(dia_semana=2)
        self.assertIsNone(terca.entrada1)
        self.assertTrue(terca.folga)

    def test_editar_horario_em_uso_avisa_que_muda_os_dias_passados(self):
        horario = _criar_horario_util(2101)
        # Duas jornadas da mesma pessoa contam uma vez; a outra pessoa já saiu deste horário.
        ana = _criar_funcionario(cpf='66611111111')
        bia = _criar_funcionario(cpf='66622222222', matricula='1002')
        FuncionarioJornada.objects.create(funcionario=ana, data_inicio=date(2026, 1, 1), horario=horario)
        FuncionarioJornada.objects.create(funcionario=ana, data_inicio=date(2026, 6, 1), horario=horario)
        FuncionarioJornada.objects.create(funcionario=bia, data_inicio=date(2026, 1, 1), horario=horario)
        FuncionarioJornada.objects.create(funcionario=bia, data_inicio=date(2026, 3, 1), horario=_criar_horario_util(2102))

        resposta = self.client.get(reverse('ponto:editar_horario', args=[horario.id]))

        self.assertContains(resposta, 'alert--aviso')
        self.assertContains(resposta, '<strong>2 funcionários</strong>')
        self.assertContains(resposta, 'muda também o cálculo dos dias passados')

    def test_horario_sem_ninguem_e_horario_novo_nao_mostram_aviso(self):
        horario = _criar_horario_util(2103)

        edicao = self.client.get(reverse('ponto:editar_horario', args=[horario.id]))
        cadastro = self.client.get(reverse('ponto:cadastrar_horario'))

        self.assertNotContains(edicao, 'alert--aviso')
        self.assertNotContains(cadastro, 'alert--aviso')

    def test_editar_horario_atualiza_dia(self):
        horario = Horario.objects.create(numero=2002, nome='Turno Editável')
        for dia in range(1, 8):
            HorarioDia.objects.create(horario=horario, dia_semana=dia)

        dados = {'numero': '2002', 'nome': 'Turno Editado'}
        dados.update(_dados_formset_horario(dia_semana_com_entrada1=3))
        # o formset agora está ligado a instâncias existentes -- inclui os ids.
        dados['dias-INITIAL_FORMS'] = '7'
        for i, dia in enumerate(horario.dias.all()):
            dados[f'dias-{i}-id'] = str(dia.id)

        resposta = self.client.post(reverse('ponto:editar_horario', args=[horario.id]), dados)

        self.assertRedirects(resposta, reverse('ponto:listar_horarios'))
        horario.refresh_from_db()
        self.assertEqual(horario.nome, 'Turno Editado')
        quarta = horario.dias.get(dia_semana=3)
        self.assertEqual(str(quarta.entrada1), '08:00:00')

    def test_excluir_horario(self):
        horario = Horario.objects.create(numero=2003, nome='Para excluir')

        resposta = self.client.post(reverse('ponto:excluir_horario', args=[horario.id]))

        self.assertRedirects(resposta, reverse('ponto:listar_horarios'))
        self.assertFalse(Horario.objects.filter(id=horario.id).exists())

    def test_saida_antes_da_entrada_nao_salva(self):
        # Caso real (horários 1014 e 1031): 15:20-15:00 virava um par atravessando a meia-noite.
        dados = {'numero': '2005', 'nome': 'Digitado errado'}
        dados.update(_dados_formset_horario())
        dados.update({
            'dias-0-entrada1': '06:00', 'dias-0-saida1': '12:00', 'dias-0-entrada2': '13:00',
            'dias-0-saida2': '15:00', 'dias-0-entrada3': '15:20', 'dias-0-saida3': '15:00',
        })

        resposta = self.client.post(reverse('ponto:cadastrar_horario'), dados)

        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(Horario.objects.filter(numero=2005).exists())
        self.assertContains(resposta, 'Da primeira entrada à última saída dá 33h00')

    def test_turno_noturno_que_vira_o_dia_salva(self):
        dados = {'numero': '2006', 'nome': '12x36 noturno', 'eh_12x36': 'on'}
        dados.update(_dados_formset_horario())
        dados.update({
            'dias-0-entrada1': '18:00', 'dias-0-saida1': '21:00', 'dias-0-entrada2': '22:00',
            'dias-0-saida2': '00:00', 'dias-0-entrada3': '00:20', 'dias-0-saida3': '06:00',
        })

        resposta = self.client.post(reverse('ponto:cadastrar_horario'), dados)

        self.assertRedirects(resposta, reverse('ponto:listar_horarios'))

    def test_lista_aponta_horario_com_saida_antes_da_entrada(self):
        # Gravado antes da validação (ou pela importação do Secullum).
        errado = Horario.objects.create(numero=2007, nome='Importado errado')
        HorarioDia.objects.create(
            horario=errado, dia_semana=1, entrada1='06:00', saida1='12:00',
            entrada2='13:00', saida2='15:00', entrada3='15:20', saida3='15:00',
        )
        _criar_horario_util(2008)

        resposta = self.client.get(reverse('ponto:listar_horarios'))

        self.assertContains(resposta, 'Confira os horários', count=2)  # tabela + cartão do celular, só o 2007

    def test_numero_duplicado_nao_permite_cadastro(self):
        Horario.objects.create(numero=2004, nome='Já existe')

        dados = {'numero': '2004', 'nome': 'Duplicado'}
        dados.update(_dados_formset_horario())

        self.client.post(reverse('ponto:cadastrar_horario'), dados)

        self.assertEqual(Horario.objects.filter(numero=2004).count(), 1)


class Horario12x36Tests(TestCase):
    """
    12x36 direto no Horario (sem Escala separada): quem trabalha em qual dia
    é decidido pela diferença de dias desde `data_inicio` da jornada (que é
    sempre um dia de trabalho), não pelo dia da semana nem pelo dia do mês.
    """

    def setUp(self):
        setor = Setor.objects.create(nome='RH')
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9999', cargo='RH', setor=setor, cpf='22222222222')
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.login(username='rhteste', password='senha123')

    def test_cadastrar_horario_12x36_pelo_checkbox(self):
        dados = {'numero': '3001', 'nome': '12x36 Teste', 'eh_12x36': 'on'}
        dados.update(_dados_formset_horario(dia_semana_com_entrada1=1))

        self.client.post(reverse('ponto:cadastrar_horario'), dados)

        horario = Horario.objects.get(numero=3001)
        self.assertTrue(horario.eh_12x36)

    def test_trabalha_no_dia_12x36_alterna_a_partir_da_data_inicio(self):
        horario = _criar_horario_12x36(3002)
        funcionario = _criar_funcionario(cpf='66666666666')
        jornada = FuncionarioJornada(funcionario=funcionario, horario=horario, data_inicio=date(2026, 8, 19))

        self.assertTrue(jornada.trabalha_no_dia_12x36(date(2026, 8, 19)))
        self.assertFalse(jornada.trabalha_no_dia_12x36(date(2026, 8, 20)))
        self.assertTrue(jornada.trabalha_no_dia_12x36(date(2026, 8, 21)))

    def test_trabalha_no_dia_12x36_nao_quebra_em_mes_de_31_dias(self):
        """
        Bug real do esquema anterior (par/ímpar pelo dia do mês): dia 31 e o
        dia 1 do mês seguinte são os dois ímpares, quebrando a alternância
        bem ali. Usando diferença de dias desde `data_inicio`, isso nunca
        acontece -- é sempre um dia sim, um dia não, não importa o mês.
        """
        horario = _criar_horario_12x36(3003)
        funcionario = _criar_funcionario(cpf='77777777777')
        jornada = FuncionarioJornada(funcionario=funcionario, horario=horario, data_inicio=date(2026, 7, 1))

        self.assertTrue(jornada.trabalha_no_dia_12x36(date(2026, 7, 31)))
        self.assertFalse(jornada.trabalha_no_dia_12x36(date(2026, 8, 1)))
        self.assertTrue(jornada.trabalha_no_dia_12x36(date(2026, 8, 2)))

    def test_pode_trocar_dia_de_folga_atribuindo_nova_jornada(self):
        horario = _criar_horario_12x36(3004)
        funcionario = _criar_funcionario(cpf='88888888889')
        FuncionarioJornada.objects.create(funcionario=funcionario, horario=horario, data_inicio=date(2026, 8, 1))
        # 02/08 seria folga pela primeira jornada -- RH decide inverter o time a partir dali.
        FuncionarioJornada.objects.create(funcionario=funcionario, horario=horario, data_inicio=date(2026, 8, 2))

        jornada_vigente = FuncionarioJornada.vigente_em(funcionario, date(2026, 8, 2))

        self.assertTrue(jornada_vigente.trabalha_no_dia_12x36(date(2026, 8, 2)))


class ImportarJornadasSecullumHelpersTests(TestCase):
    """Normalização usada pelo comando pra casar funcionário do RH com o do Secullum (CPF/nome)."""

    def test_so_digitos_remove_pontuacao_do_cpf(self):
        from ponto.management.commands.importar_jornadas_secullum import _so_digitos

        self.assertEqual(_so_digitos('401.446.661-20'), '40144666120')
        self.assertEqual(_so_digitos(None), '')

    def test_normaliza_nome_ignora_acento_caixa_e_espaco_duplo(self):
        from ponto.management.commands.importar_jornadas_secullum import _normaliza_nome

        self.assertEqual(_normaliza_nome('José  da Conceição'), 'JOSE DA CONCEICAO')
        self.assertEqual(_normaliza_nome(' Maria Antônia '), 'MARIA ANTONIA')


class ReconciliarFuncionariosEquipamentoHelpersTests(TestCase):
    """Normalização usada pra casar o cadastro já existente no equipamento com o funcionário do RH."""

    def test_so_digitos_remove_prefixo_estranho_do_secullum(self):
        from ponto.management.commands.reconciliar_funcionarios_equipamento import _so_digitos

        # Visto de verdade no Secullum: cpf/n_pis com um "d" grudado na frente.
        self.assertEqual(_so_digitos('d15524110520'), '15524110520')
        self.assertEqual(_so_digitos(15524110520), '15524110520')

    def test_normaliza_nome(self):
        from ponto.management.commands.reconciliar_funcionarios_equipamento import _normaliza_nome

        self.assertEqual(_normaliza_nome('João  Vitor'), 'JOAO VITOR')


class VincularMarcacoesSemFuncionarioTests(TestCase):
    """
    Marcações coletadas antes de sabermos o PIS de alguém ficam com
    funcionario=None pra sempre, a não ser que sejam revinculadas depois --
    a coleta em si é só incremental, não reprocessa o que já foi salvo.
    """

    def _marcacao_sem_funcionario(self, equipamento, identificador, nsr='1'):
        return MarcacaoRelogio.objects.create(
            equipamento=equipamento, funcionario=None, id_evento_dispositivo=nsr,
            momento='2026-08-01T08:00:00Z', dados_brutos={'identificador': identificador},
        )

    def test_vincula_por_cpf_ou_pis(self):
        equipamento = _criar_equipamento()
        funcionario_cpf = _criar_funcionario(cpf='11111111111', matricula='1001')
        funcionario_pis = _criar_funcionario(cpf='22222222222', pis='33333333333', matricula='1002')
        m1 = self._marcacao_sem_funcionario(equipamento, '11111111111', nsr='1')
        m2 = self._marcacao_sem_funcionario(equipamento, '33333333333', nsr='2')
        m3 = self._marcacao_sem_funcionario(equipamento, '99999999999', nsr='3')  # ninguém conhecido

        call_command('vincular_marcacoes_sem_funcionario')

        m1.refresh_from_db(); m2.refresh_from_db(); m3.refresh_from_db()
        self.assertEqual(m1.funcionario, funcionario_cpf)
        self.assertEqual(m2.funcionario, funcionario_pis)
        self.assertIsNone(m3.funcionario)

    def test_vincula_pelo_identificador_que_o_relogio_usa_pra_pessoa(self):
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario(cpf='70819163171')
        SincronizacaoFuncionario.objects.update_or_create(
            funcionario=funcionario, equipamento=equipamento,
            defaults={'id_usuario_dispositivo': '870819163173', 'status': 'sincronizado'},
        )
        marcacao = MarcacaoRelogio.objects.create(
            equipamento=equipamento, funcionario=None, id_evento_dispositivo='1', momento='2026-10-09T10:16:00Z',
            dados_brutos={'prefixo': '8', 'identificador': '70819163173'},
        )

        call_command('vincular_marcacoes_sem_funcionario')

        marcacao.refresh_from_db()
        self.assertEqual(marcacao.funcionario, funcionario)

    def test_dry_run_nao_grava(self):
        equipamento = _criar_equipamento()
        funcionario = _criar_funcionario(cpf='11111111111')
        marcacao = self._marcacao_sem_funcionario(equipamento, '11111111111')

        call_command('vincular_marcacoes_sem_funcionario', '--dry-run')

        marcacao.refresh_from_db()
        self.assertIsNone(marcacao.funcionario)

    def test_filtro_por_equipamento(self):
        eq1 = _criar_equipamento(nome='Equip 1', ip='192.168.1.61')
        eq2 = _criar_equipamento(nome='Equip 2', ip='192.168.1.62')
        funcionario = _criar_funcionario(cpf='11111111111')
        m1 = self._marcacao_sem_funcionario(eq1, '11111111111', nsr='1')
        m2 = self._marcacao_sem_funcionario(eq2, '11111111111', nsr='2')

        call_command('vincular_marcacoes_sem_funcionario', '--equipamento', 'Equip 1')

        m1.refresh_from_db(); m2.refresh_from_db()
        self.assertEqual(m1.funcionario, funcionario)
        self.assertIsNone(m2.funcionario)

    def test_inicio_da_revinculacao_e_o_primeiro_dia_do_mes_anterior(self):
        self.assertEqual(timezone.localtime(inicio_da_revinculacao(date(2026, 10, 27))).date(), date(2026, 9, 1))
        self.assertEqual(timezone.localtime(inicio_da_revinculacao(date(2026, 1, 1))).date(), date(2025, 12, 1))

    def test_resumo_agrupa_por_equipamento_e_identificador(self):
        adm = _criar_equipamento(nome='PONTO ADM', ip='192.168.1.71')
        funcionario = _criar_funcionario(cpf='11111111111')
        agora = timezone.now()
        for nsr, prefixo, identificador in [
            ('1', '0', '23776484426'), ('2', '0', '23776484426'), ('3', '8', '70819163173'),
        ]:
            MarcacaoRelogio.objects.create(
                equipamento=adm, funcionario=None, id_evento_dispositivo=nsr, momento=agora,
                dados_brutos={'prefixo': prefixo, 'identificador': identificador},
            )
        MarcacaoRelogio.objects.create(  # com dono: não entra
            equipamento=adm, funcionario=funcionario, id_evento_dispositivo='4', momento=agora,
            dados_brutos={'prefixo': '0', 'identificador': '11111111111'},
        )

        resumo = resumo_marcacoes_sem_funcionario(agora - timedelta(days=1))

        self.assertEqual(
            [(linha['equipamento'], linha['identificador'], linha['quantidade']) for linha in resumo],
            [('PONTO ADM', '23776484426', 2), ('PONTO ADM', '870819163173', 1)],
        )


class EnfileirarFuncionariosExistentesTests(TestCase):
    """
    Funcionários que já existiam antes do módulo de Ponto ser instalado não
    recebem o signal automaticamente -- este comando cobre esse backfill,
    necessário ao implantar num banco com funcionários já cadastrados.
    """

    def test_enfileira_pendencia_para_funcionario_que_ja_existia(self):
        # simula um funcionário que já existia ANTES de qualquer equipamento
        # ser cadastrado (o post_save original não achou nenhum ativo na hora)
        funcionario = Funcionario.objects.create(
            nome='Antigo', matricula='500', cargo='Operador',
            setor=Setor.objects.create(nome='Produção'), cpf='44444444444',
        )
        equipamento = _criar_equipamento()
        self.assertEqual(SincronizacaoFuncionario.objects.filter(funcionario=funcionario).count(), 0)

        call_command('enfileirar_funcionarios_existentes')

        sincronizacao = SincronizacaoFuncionario.objects.get(funcionario=funcionario, equipamento=equipamento)
        self.assertEqual(sincronizacao.status, 'pendente')


def _criar_horario_util(numero, entrada1='08:00', saida1='12:00', entrada2='13:00', saida2='17:00'):
    """Jornada fixa segunda a sexta (08-12/13-17 = 8h/dia), sábado/domingo de folga."""
    horario = Horario.objects.create(numero=numero, nome=f'Jornada {numero}')
    for dia in range(1, 6):
        HorarioDia.objects.create(horario=horario, dia_semana=dia, entrada1=entrada1, saida1=saida1, entrada2=entrada2, saida2=saida2)
    for dia in (6, 7):
        HorarioDia.objects.create(horario=horario, dia_semana=dia)
    return horario


def _criar_horario_12x36(numero, entrada1='06:00', saida1='18:00'):
    """
    Turno de 12h diurno em todos os dias da semana (mesmo padrão do horário
    1001 real importado do Secullum) -- quem trabalha em qual dia é decidido
    pela `data_inicio` da jornada do funcionário (`trabalha_no_dia_12x36`),
    não por aqui. Turno virando a noite: ver `_criar_horario_12x36_noturno`.
    """
    horario = Horario.objects.create(numero=numero, nome=f'12x36 {numero}', eh_12x36=True)
    for dia in range(1, 8):
        HorarioDia.objects.create(horario=horario, dia_semana=dia, entrada1=entrada1, saida1=saida1)
    return horario


def _criar_horario_12x36_noturno(numero):
    """
    12x36 noturno com intervalo, igual em todos os dias: 18:00-22:00 e 23:00-06:00
    (11h esperadas). A entrada é num dia e a última saída, no dia seguinte.
    """
    horario = Horario.objects.create(numero=numero, nome=f'12x36 noturno {numero}', eh_12x36=True)
    for dia in range(1, 8):
        HorarioDia.objects.create(
            horario=horario, dia_semana=dia, entrada1='18:00', saida1='22:00', entrada2='23:00', saida2='06:00',
        )
    return horario


def _momento_local(dia, hhmm):
    hora, minuto = map(int, hhmm.split(':'))
    return timezone.make_aware(datetime(dia.year, dia.month, dia.day, hora, minuto))


def _bater_ponto(funcionario, equipamento, dia, *horarios):
    """Cria MarcacaoRelogio em sequência num dia, a partir de horas 'HH:MM'."""
    marcacoes = []
    for i, hhmm in enumerate(horarios):
        hora, minuto = map(int, hhmm.split(':'))
        momento = timezone.make_aware(datetime(dia.year, dia.month, dia.day, hora, minuto))
        marcacoes.append(MarcacaoRelogio.objects.create(
            equipamento=equipamento, funcionario=funcionario,
            id_evento_dispositivo=f'{funcionario.pk}-{dia.isoformat()}-{i}', momento=momento,
        ))
    return marcacoes


class CalculosTests(TestCase):
    """Motor de cálculo (ponto/calculos.py): cada regra confirmada com o usuário, isolada."""

    def setUp(self):
        self.equipamento = _criar_equipamento()
        self.funcionario = _criar_funcionario(cpf='55555555555')

    def test_dia_normal_sem_diferenca_saldo_zero(self):
        horario = _criar_horario_util(4001)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)  # segunda-feira
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:00', '12:00', '13:00', '17:00')

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        self.assertEqual(resultado['dias'][0]['saldo_segundos'], 0)
        self.assertEqual(resultado['totais']['extra_50_segundos'], 0)
        self.assertEqual(resultado['totais']['falta_segundos'], 0)

    def test_hora_extra_dia_util_e_taxa_50(self):
        horario = _criar_horario_util(4002)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:00', '12:00', '13:00', '18:00')  # 1h extra

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        self.assertEqual(resultado['totais']['extra_50_segundos'], 3600)
        self.assertEqual(resultado['totais']['extra_100_segundos'], 0)

        dia = resultado['dias'][0]
        self.assertEqual(dia['segundos_normais'], 8 * 3600)  # Normais nunca passa do esperado
        self.assertEqual(dia['segundos_extra_50_dia'], 3600)
        self.assertEqual(dia['segundos_extra_100_dia'], 0)
        self.assertEqual(dia['segundos_falta_dia'], 0)
        self.assertEqual(len(dia['pares']), 2)  # só usou 2 pares nesse dia -- não preenche até 5 à toa
        pares_preenchidos = [p for p in dia['pares'] if p[0] is not None]
        self.assertEqual(len(pares_preenchidos), 2)

    def test_hora_extra_domingo_e_taxa_100(self):
        horario = _criar_horario_util(4003)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        domingo = date(2026, 8, 2)  # domingo -- folga na jornada, mas ele foi e trabalhou
        _bater_ponto(self.funcionario, self.equipamento, domingo, '08:00', '12:00')  # 4h trabalhadas, 0 esperadas

        resultado = calcular_periodo(self.funcionario, domingo, domingo)

        self.assertEqual(resultado['totais']['extra_100_segundos'], 4 * 3600)
        self.assertEqual(resultado['totais']['extra_50_segundos'], 0)

    def test_hora_extra_feriado_e_taxa_100(self):
        horario = _criar_horario_util(4004)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        sexta_feriado = date(2026, 9, 4)  # sexta-feira comum, marcada como feriado
        Feriado.objects.create(data=sexta_feriado, descricao='Feriado de teste')
        _bater_ponto(self.funcionario, self.equipamento, sexta_feriado, '08:00', '12:00', '13:00', '19:00')  # 2h extra

        resultado = calcular_periodo(self.funcionario, sexta_feriado, sexta_feriado)

        self.assertEqual(resultado['totais']['extra_100_segundos'], 2 * 3600)
        self.assertEqual(resultado['totais']['extra_50_segundos'], 0)

    def test_12x36_sempre_50_por_cento_mesmo_no_domingo(self):
        domingo = date(2026, 8, 2)  # domingo, dia de trabalho do ciclo dele
        horario = _criar_horario_12x36(4005)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=domingo, horario=horario)
        _bater_ponto(self.funcionario, self.equipamento, domingo, '06:00', '19:00')  # 1h extra (13h trabalhadas)

        resultado = calcular_periodo(self.funcionario, domingo, domingo)

        self.assertEqual(resultado['totais']['extra_50_segundos'], 3600)
        self.assertEqual(resultado['totais']['extra_100_segundos'], 0)

    def test_12x36_folga_no_dia_seguinte_ao_inicio_do_ciclo(self):
        horario = _criar_horario_12x36(4008)
        FuncionarioJornada.objects.create(
            funcionario=self.funcionario, data_inicio=date(2026, 8, 2), horario=horario,
        )
        dia_de_folga = date(2026, 8, 3)  # 1 dia depois do início do ciclo -- folga

        resultado = calcular_periodo(self.funcionario, dia_de_folga, dia_de_folga)

        self.assertTrue(resultado['dias'][0]['folga'])
        self.assertEqual(resultado['dias'][0]['segundos_esperados'], 0)

    def test_compensacao_extra_abate_falta_mesma_taxa(self):
        horario = _criar_horario_util(4006)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        terca = date(2026, 8, 4)
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:00', '12:00', '13:00', '19:00')  # +2h
        _bater_ponto(self.funcionario, self.equipamento, terca, '09:00', '12:00', '13:00', '17:00')  # -1h

        resultado = calcular_periodo(self.funcionario, segunda, terca)

        self.assertEqual(resultado['totais']['extra_50_segundos'], 3600)  # 2h extra - 1h compensada = 1h
        self.assertEqual(resultado['totais']['falta_segundos'], 0)

    def test_atraso_dentro_da_tolerancia_nao_gera_falta(self):
        horario = _criar_horario_util(4015)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:04', '12:00', '13:00', '17:00')  # 4 min a menos

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        dia = resultado['dias'][0]
        self.assertEqual(dia['segundos_falta_dia'], 0)
        self.assertEqual(dia['saldo_segundos'], 0)
        self.assertEqual(dia['segundos_normais'], 8 * 3600 - 4 * 60)  # Normais = só o trabalhado de fato
        self.assertEqual(resultado['totais']['falta_segundos'], 0)
        self.assertEqual(situacao_do_dia(dia, hoje=date(2026, 8, 10)), ('OK', 'aprovado', False))

    def test_atraso_de_exatamente_15_minutos_ainda_fica_na_tolerancia(self):
        horario = _criar_horario_util(4016)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        # 15 min no dia somando duas batidas (10 na entrada + 5 na volta do almoço).
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:10', '12:00', '13:05', '17:00')

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        self.assertEqual(resultado['dias'][0]['segundos_falta_dia'], 0)
        self.assertEqual(resultado['totais']['falta_segundos'], 0)

    def test_atraso_acima_da_tolerancia_conta_inteiro(self):
        horario = _criar_horario_util(4017)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:20', '12:00', '13:00', '17:00')  # 20 min a menos

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        dia = resultado['dias'][0]
        self.assertEqual(dia['segundos_falta_dia'], 20 * 60)  # os 20 minutos, não só os 5 além da tolerância
        self.assertEqual(resultado['totais']['falta_segundos'], 20 * 60)
        self.assertEqual(situacao_do_dia(dia, hoje=date(2026, 8, 10))[0], 'Falta/atraso')

    def test_extra_dentro_da_tolerancia_nao_conta(self):
        horario = _criar_horario_util(4018)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:00', '12:00', '13:00', '17:10')  # 10 min a mais

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        dia = resultado['dias'][0]
        self.assertEqual(dia['segundos_extra_50_dia'], 0)
        self.assertEqual(dia['saldo_segundos'], 0)
        self.assertEqual(resultado['totais']['extra_50_segundos'], 0)

    def test_extra_de_exatamente_15_minutos_ainda_fica_na_tolerancia(self):
        horario = _criar_horario_util(4019)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        _bater_ponto(self.funcionario, self.equipamento, segunda, '07:50', '12:00', '13:00', '17:05')  # 10 + 5 = 15 a mais

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        self.assertEqual(resultado['dias'][0]['segundos_extra_50_dia'], 0)
        self.assertEqual(resultado['totais']['extra_50_segundos'], 0)

    def test_extra_acima_da_tolerancia_conta_inteiro(self):
        horario = _criar_horario_util(4020)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:00', '12:00', '13:00', '17:16')  # 16 min a mais

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        self.assertEqual(resultado['dias'][0]['segundos_extra_50_dia'], 16 * 60)  # os 16, não só 1 além da tolerância
        self.assertEqual(resultado['totais']['extra_50_segundos'], 16 * 60)

    def test_atraso_compensado_na_saida_fica_na_tolerancia(self):
        # Confirmado com o usuário: 30 min de atraso e 20 a mais na saída = 10 min no
        # dia, dentro da tolerância -- sem desconto, mesmo o atraso sozinho passando de 15.
        horario = _criar_horario_util(4026)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:30', '12:00', '13:00', '17:20')

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        self.assertEqual(resultado['dias'][0]['saldo_segundos'], 0)
        self.assertEqual(resultado['totais']['falta_segundos'], 0)

    def test_tolerancia_nao_vale_em_domingo_de_folga(self):
        horario = _criar_horario_util(4021)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        domingo = date(2026, 8, 2)  # folga na jornada: sem horário previsto
        _bater_ponto(self.funcionario, self.equipamento, domingo, '08:00', '08:10')

        resultado = calcular_periodo(self.funcionario, domingo, domingo)

        self.assertEqual(resultado['dias'][0]['segundos_extra_100_dia'], 10 * 60)  # todo minuto conta
        self.assertEqual(resultado['totais']['extra_100_segundos'], 10 * 60)

    def test_tolerancia_nao_vale_em_feriado(self):
        horario = _criar_horario_util(4022)
        horario.considerar_feriados = True
        horario.save()
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        feriado = date(2026, 9, 7)  # segunda-feira
        Feriado.objects.create(data=feriado, descricao='Independência')
        _bater_ponto(self.funcionario, self.equipamento, feriado, '08:00', '08:10')

        resultado = calcular_periodo(self.funcionario, feriado, feriado)

        self.assertEqual(resultado['dias'][0]['segundos_extra_100_dia'], 10 * 60)
        self.assertEqual(resultado['totais']['extra_100_segundos'], 10 * 60)

    def test_batida_repetida_no_mesmo_minuto_conta_uma_vez(self):
        horario = _criar_horario_util(4023)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        # Bateu três vezes às 12:00 e o relógio gravou as três (NSRs seguidos).
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:00', '12:00', '12:00', '12:00', '13:00', '17:00')

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        dia = resultado['dias'][0]
        self.assertEqual(len(dia['marcacoes']), 4)
        self.assertFalse(dia['marcacao_impar'])
        self.assertEqual(dia['segundos_trabalhados'], 8 * 3600)  # o almoço não vira tempo trabalhado
        self.assertEqual(MarcacaoRelogio.objects.filter(funcionario=self.funcionario).count(), 6)  # nada apagado

    def test_batida_um_minuto_depois_nao_e_repetida(self):
        horario = _criar_horario_util(4024)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:00', '12:00', '12:01', '13:00', '17:00')

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        dia = resultado['dias'][0]
        self.assertEqual(len(dia['marcacoes']), 5)  # só o mesmo minuto é ignorado
        self.assertTrue(dia['marcacao_impar'])

    def test_corrigir_a_batida_que_ficou_nao_traz_a_repetida_de_volta(self):
        horario = _criar_horario_util(4025)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        marcacoes = _bater_ponto(self.funcionario, self.equipamento, segunda, '08:00', '12:12', '12:12', '13:00', '17:00')
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=segunda, tipo_evento='saida',
            horario_ajustado='12:00:00', marcacao_original=marcacoes[1],  # a que ficou na conta
            motivo='A saída pro almoço foi 12:00.', status='aprovado',
        )

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        dia = resultado['dias'][0]
        self.assertEqual(
            [e.momento for e in dia['marcacoes']],
            [_momento_local(segunda, h) for h in ('08:00', '12:00', '13:00', '17:00')],
        )
        self.assertEqual(dia['segundos_trabalhados'], 8 * 3600)

    def test_marcacao_impar_e_sinalizada_sem_quebrar(self):
        horario = _criar_horario_util(4007)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:00', '12:00', '13:00')  # esqueceu de bater a saída

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        self.assertTrue(resultado['dias'][0]['marcacao_impar'])
        self.assertEqual(resultado['totais']['dias_com_marcacao_impar'], 1)
        # o par completo (08-12) ainda soma normalmente, a batida solta (13:00) não quebra o cálculo
        self.assertEqual(resultado['dias'][0]['segundos_trabalhados'], 4 * 3600)

    def test_funcionario_sem_jornada_e_sinalizado(self):
        segunda = date(2026, 8, 3)

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        self.assertTrue(resultado['dias'][0]['sem_jornada'])
        self.assertEqual(resultado['totais']['dias_sem_jornada'], 1)

    def test_ajuste_manual_lanca_batida_que_faltou(self):
        horario = _criar_horario_util(4009)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:00', '12:00', '13:00')  # esqueceu a saída
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=segunda, tipo_evento='saida',
            horario_ajustado='17:00:00', motivo='Esqueceu de bater a saída.', status='aprovado',
        )

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        dia = resultado['dias'][0]
        self.assertFalse(dia['marcacao_impar'])
        self.assertEqual(dia['segundos_trabalhados'], 8 * 3600)
        ultimo_par = dia['pares'][1]
        self.assertTrue(ultimo_par[1].manual)

    def test_ajuste_manual_corrige_batida_existente_sem_apagar_a_original(self):
        horario = _criar_horario_util(4010)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        marcacoes = _bater_ponto(self.funcionario, self.equipamento, segunda, '08:00', '12:00', '13:00', '16:00')  # saída errada
        saida_errada = marcacoes[-1]
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=segunda, tipo_evento='saida',
            horario_ajustado='17:00:00', marcacao_original=saida_errada,
            motivo='Relógio registrou 16:00, correto é 17:00.', status='aprovado',
        )

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        dia = resultado['dias'][0]
        self.assertEqual(dia['segundos_trabalhados'], 8 * 3600)  # usa a correção (17:00), não o 16:00 do relógio
        self.assertTrue(MarcacaoRelogio.objects.filter(id=saida_errada.id).exists())  # original preservada

    def test_ajuste_pendente_ou_rejeitado_nao_conta_no_calculo(self):
        horario = _criar_horario_util(4011)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:00', '12:00', '13:00')  # esqueceu a saída
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=segunda, tipo_evento='saida',
            horario_ajustado='17:00:00', motivo='Pendente.', status='pendente',
        )
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=segunda, tipo_evento='saida',
            horario_ajustado='17:30:00', motivo='Rejeitado.', status='rejeitado',
        )

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        dia = resultado['dias'][0]
        self.assertTrue(dia['marcacao_impar'])  # a saída continua "faltando" pro cálculo
        self.assertEqual(dia['segundos_trabalhados'], 4 * 3600)  # só o par 08-12 conta

    def test_dia_em_ferias_aprovadas_nao_desconta_falta(self):
        horario = _criar_horario_util(4012)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)  # dia útil, sem nenhuma marcação -- faltaria em condições normais
        SolicitacaoFerias.objects.create(
            funcionario=self.funcionario, data_solicitada=date(2026, 8, 3), dias_solicitados=5, status='aprovada',
        )

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        dia = resultado['dias'][0]
        self.assertTrue(dia['ferias'])
        self.assertFalse(dia['atestado'])
        self.assertEqual(dia['segundos_esperados'], 0)
        self.assertEqual(dia['segundos_falta_dia'], 0)

    def test_ferias_pendente_nao_conta_como_ferias(self):
        horario = _criar_horario_util(4013)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        SolicitacaoFerias.objects.create(
            funcionario=self.funcionario, data_solicitada=date(2026, 8, 3), dias_solicitados=5, status='pendente_gestor',
        )

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        dia = resultado['dias'][0]
        self.assertFalse(dia['ferias'])
        self.assertEqual(dia['segundos_falta_dia'], 8 * 3600)  # falta normal, já que a solicitação não foi aprovada

    def test_dia_com_atestado_nao_desconta_falta(self):
        horario = _criar_horario_util(4014)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        segunda = date(2026, 8, 3)
        Atestado.objects.create(funcionario=self.funcionario, data_inicio=segunda, data_fim=segunda, motivo='Gripe')

        resultado = calcular_periodo(self.funcionario, segunda, segunda)

        dia = resultado['dias'][0]
        self.assertTrue(dia['atestado'])
        self.assertFalse(dia['ferias'])
        self.assertEqual(dia['segundos_esperados'], 0)
        self.assertEqual(dia['segundos_falta_dia'], 0)


class DiaFaltaTests(TestCase):
    """Dia de trabalho sem nenhuma batida: fora da compensação, desconta o DSR da semana e tira a assiduidade."""

    def setUp(self):
        self.equipamento = _criar_equipamento()
        self.funcionario = _criar_funcionario(cpf='55555555555')
        FuncionarioJornada.objects.create(
            funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=_criar_horario_util(4901),
        )
        self.segunda = date(2026, 8, 3)
        self.terca = date(2026, 8, 4)

    def test_hora_extra_nao_compensa_dia_falta(self):
        _bater_ponto(self.funcionario, self.equipamento, self.segunda, '08:00', '12:00', '13:00', '19:00')  # +2h
        # terça: não veio

        resultado = calcular_periodo(self.funcionario, self.segunda, self.terca)

        totais = resultado['totais']
        self.assertTrue(resultado['dias'][1]['dia_falta'])
        self.assertEqual(totais['extra_50_segundos'], 2 * 3600)  # as 2h continuam a pagar
        self.assertEqual(totais['falta_segundos'], 0)  # o dia não vira horas de falta
        self.assertEqual(totais['dias_falta'], 1)
        self.assertEqual(totais['dsr_perdidos'], 1)
        self.assertTrue(totais['perdeu_assiduidade'])

    def test_falta_parcial_continua_compensavel(self):
        _bater_ponto(self.funcionario, self.equipamento, self.segunda, '08:00', '12:00', '13:00', '19:00')  # +2h
        _bater_ponto(self.funcionario, self.equipamento, self.terca, '08:00', '12:00')  # -4h, mas veio

        totais = calcular_periodo(self.funcionario, self.segunda, self.terca)['totais']

        self.assertEqual(totais['extra_50_segundos'], 0)
        self.assertEqual(totais['falta_segundos'], 2 * 3600)
        self.assertEqual(totais['dias_falta'], 0)
        self.assertFalse(totais['perdeu_assiduidade'])

    def test_varias_faltas_na_mesma_semana_perdem_um_dsr(self):
        totais = calcular_periodo(self.funcionario, self.segunda, date(2026, 8, 6))['totais']  # segunda a quinta

        self.assertEqual(totais['dias_falta'], 4)
        self.assertEqual(totais['dsr_perdidos'], 1)

    def test_faltas_em_semanas_diferentes_perdem_um_dsr_cada(self):
        sexta, segunda_seguinte = date(2026, 8, 7), date(2026, 8, 10)

        totais = calcular_periodo(self.funcionario, sexta, segunda_seguinte)['totais']

        self.assertEqual(totais['dias_falta'], 2)  # sábado e domingo são folga
        self.assertEqual(totais['dsr_perdidos'], 2)

    def test_marcacao_impar_nao_e_dia_falta(self):
        _bater_ponto(self.funcionario, self.equipamento, self.segunda, '08:00')  # veio, faltou bater o resto

        resultado = calcular_periodo(self.funcionario, self.segunda, self.segunda)

        self.assertFalse(resultado['dias'][0]['dia_falta'])
        self.assertEqual(resultado['totais']['dias_falta'], 0)

    def test_atestado_nao_e_dia_falta(self):
        Atestado.objects.create(funcionario=self.funcionario, data_inicio=self.segunda, data_fim=self.segunda, motivo='Gripe')

        totais = calcular_periodo(self.funcionario, self.segunda, self.segunda)['totais']

        self.assertEqual(totais['dias_falta'], 0)
        self.assertFalse(totais['perdeu_assiduidade'])

    def test_hoje_sem_batida_ainda_nao_e_dia_falta(self):
        hoje = timezone.localdate()
        # 12x36 começando hoje: hoje é dia de trabalho, caia no dia da semana que cair.
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=hoje, horario=_criar_horario_12x36(4902))

        resultado = calcular_periodo(self.funcionario, hoje, hoje)

        self.assertFalse(resultado['dias'][0]['dia_falta'])
        self.assertEqual(resultado['totais']['dias_falta'], 0)


class HoraNoturnaTests(TestCase):
    """
    Trabalho entre 22:00 e 05:00: no relógio no dia (esperado, saldo, extras, faltas),
    na hora de 52min30s só no total convertido do período.
    """

    # As 6h de relógio no noturno do `_criar_horario_12x36_noturno` (23:00-05:00).
    NOTURNAS_DO_TURNO = 6 * 3600

    def setUp(self):
        self.equipamento = _criar_equipamento()
        self.funcionario = _criar_funcionario(cpf='55555555555')
        self.dia = date(2026, 8, 3)

    def _turno_noturno(self, *batidas_do_dia, saida='06:00'):
        FuncionarioJornada.objects.create(
            funcionario=self.funcionario, data_inicio=self.dia, horario=_criar_horario_12x36_noturno(4601),
        )
        _bater_ponto(self.funcionario, self.equipamento, self.dia, *batidas_do_dia)
        _bater_ponto(self.funcionario, self.equipamento, self.dia + timedelta(days=1), saida)
        return calcular_periodo(self.funcionario, self.dia, self.dia)['dias'][0]

    def test_turno_noturno_fica_no_relogio(self):
        dia = self._turno_noturno('18:00', '22:00', '23:00')

        self.assertEqual(dia['segundos_trabalhados'], 11 * 3600)
        self.assertEqual(dia['segundos_esperados'], 11 * 3600)
        self.assertEqual(dia['segundos_normais'], 11 * 3600)
        self.assertEqual(dia['segundos_noturnos'], self.NOTURNAS_DO_TURNO)
        self.assertEqual(dia['saldo_segundos'], 0)

    def test_tolerancia_no_noturno(self):
        # Voltou do intervalo às 23:14: 14 min de atraso -- fica na tolerância.
        dia = self._turno_noturno('18:00', '22:00', '23:14')

        self.assertEqual(dia['saldo_segundos'], 0)

    def test_atraso_no_noturno_conta_no_relogio(self):
        # Voltou às 23:35: 35 min de falta, sem a redução da hora noturna.
        dia = self._turno_noturno('18:00', '22:00', '23:35')

        self.assertEqual(dia['segundos_falta_dia'], 35 * 60)

    def test_trabalho_depois_das_5h_e_diurno(self):
        # Esticou até 07:00: a 1h a mais é depois das 05:00, então é extra diurna.
        dia = self._turno_noturno('18:00', '22:00', '23:00', saida='07:00')

        self.assertEqual(dia['segundos_noturnos'], self.NOTURNAS_DO_TURNO)
        self.assertEqual(dia['segundos_extra_50_dia'], 3600)

    def test_hora_extra_noturna_conta_no_relogio_e_o_total_convertido_na_hora_de_52min30s(self):
        FuncionarioJornada.objects.create(
            funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=_criar_horario_util(4602),
        )
        # Jornada inteira e mais 1h45 de relógio depois das 22:00.
        _bater_ponto(self.funcionario, self.equipamento, self.dia, '08:00', '12:00', '13:00', '17:00', '22:00', '23:45')

        resultado = calcular_periodo(self.funcionario, self.dia, self.dia)

        dia = resultado['dias'][0]
        self.assertEqual(dia['segundos_extra_50_dia'], 105 * 60)
        self.assertEqual(dia['segundos_noturnos'], 105 * 60)
        self.assertEqual(resultado['totais']['noturno_segundos'], 105 * 60)
        self.assertEqual(resultado['totais']['noturno_convertido_segundos'], 2 * 3600)  # 1h45 x 8/7


class ValoresDoPeriodoTests(SimpleTestCase):
    """Totais do período em R$ (`calculos.valores_do_periodo`)."""

    def _totais(self, **valores):
        totais = {
            'extra_50_segundos': 0, 'extra_100_segundos': 0, 'noturno_segundos': 0,
            'noturno_convertido_segundos': 0, 'falta_segundos': 0, 'dias_falta': 0, 'dsr_perdidos': 0,
        }
        totais.update(valores)
        return totais

    def test_adicional_noturno_e_20_por_cento_da_hora_convertida(self):
        valores = valores_do_periodo(
            self._totais(noturno_segundos=7 * 3600, noturno_convertido_segundos=8 * 3600), Decimal('2200.00'),
        )

        self.assertEqual(valores['adicional_noturno'], Decimal('16.00'))  # 10 x 20% x 8h
        self.assertEqual(valores['a_pagar'], Decimal('16.00'))

    def test_hora_e_o_salario_dividido_por_220(self):
        valores = valores_do_periodo(
            self._totais(extra_50_segundos=2 * 3600, extra_100_segundos=3600, falta_segundos=30 * 60), Decimal('2200.00'),
        )

        self.assertEqual(valores['valor_hora'], Decimal('10.00'))
        self.assertEqual(valores['extra_50'], Decimal('30.00'))  # 10 x 1,5 x 2h
        self.assertEqual(valores['extra_100'], Decimal('20.00'))  # 10 x 2 x 1h
        self.assertEqual(valores['falta'], Decimal('5.00'))  # 10 x 0,5h
        self.assertEqual(valores['a_pagar'], Decimal('50.00'))

    def test_dia_falta_e_dsr_valem_um_trinta_avos_do_salario_cada(self):
        valores = valores_do_periodo(self._totais(dias_falta=2, dsr_perdidos=1), Decimal('3000.00'))

        self.assertEqual(valores['dias_falta'], Decimal('200.00'))
        self.assertEqual(valores['dsr'], Decimal('100.00'))
        self.assertEqual(valores['a_descontar'], Decimal('300.00'))

    def test_arredonda_no_centavo(self):
        valores = valores_do_periodo(self._totais(extra_50_segundos=3600), Decimal('1621.00'))

        self.assertEqual(valores['extra_50'], Decimal('11.05'))  # 1621 / 220 x 1,5 = 11,0522...

    def test_sem_salario_cadastrado_nao_calcula(self):
        self.assertIsNone(valores_do_periodo(self._totais(), None))


class IntervaloCurtoTests(TestCase):
    """Almoço abaixo de 1h (`calculos.intervalo_curto`), usado no aviso diário ao RH e ao gestor."""

    def setUp(self):
        self.equipamento = _criar_equipamento()
        self.funcionario = _criar_funcionario(cpf='55555555555')
        FuncionarioJornada.objects.create(
            funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=_criar_horario_util(4701),
        )
        self.segunda = date(2026, 8, 3)

    def _intervalo_curto(self, *horarios):
        _bater_ponto(self.funcionario, self.equipamento, self.segunda, *horarios)
        return intervalo_curto(calcular_periodo(self.funcionario, self.segunda, self.segunda)['dias'][0])

    def test_almoco_abaixo_de_1h_e_curto(self):
        saida, volta = self._intervalo_curto('08:00', '12:00', '12:40', '17:20')

        self.assertEqual((saida.momento, volta.momento), (_momento_local(self.segunda, '12:00'), _momento_local(self.segunda, '12:40')))

    def test_59_minutos_ja_e_curto(self):
        self.assertIsNotNone(self._intervalo_curto('08:00', '12:00', '12:59', '17:00'))

    def test_almoco_de_1h_nao_e_curto(self):
        self.assertIsNone(self._intervalo_curto('08:00', '12:00', '13:00', '17:00'))

    def test_almoco_e_o_maior_intervalo_e_nao_o_cafe(self):
        # Almoço 50 min + café 20 min: o aviso é sobre o almoço.
        saida, volta = self._intervalo_curto('08:00', '12:00', '12:50', '15:00', '15:20', '18:00')

        self.assertEqual(volta.momento - saida.momento, timedelta(minutes=50))

    def test_cafe_curto_com_almoco_de_1h12_nao_avisa(self):
        self.assertIsNone(self._intervalo_curto('08:00', '12:00', '13:12', '15:00', '15:20', '18:00'))

    def test_dia_de_ate_6h_nao_exige_1h(self):
        self.assertIsNone(self._intervalo_curto('08:00', '11:00', '11:15', '14:00'))  # 5h45 trabalhadas

    def test_de_4h_a_6h_exige_15_min(self):
        saida, volta = self._intervalo_curto('08:00', '11:00', '11:10', '13:30')  # 5h20 trabalhadas

        self.assertEqual(volta.momento - saida.momento, timedelta(minutes=10))

    def test_6h_exatas_exigem_15_min_e_nao_1h(self):
        self.assertIsNone(self._intervalo_curto('08:00', '11:00', '11:20', '14:20'))

    def test_ate_4h_nao_exige_intervalo(self):
        self.assertIsNone(self._intervalo_curto('08:00', '10:00', '10:05', '12:00'))  # 3h55 trabalhadas

    def test_intervalo_minimo_pelas_horas_trabalhadas(self):
        self.assertEqual(intervalo_minimo(4 * 3600), 0)
        self.assertEqual(intervalo_minimo(4 * 3600 + 1), 15 * 60)
        self.assertEqual(intervalo_minimo(6 * 3600), 15 * 60)
        self.assertEqual(intervalo_minimo(6 * 3600 + 1), 60 * 60)

    def test_sem_intervalo_batido_fica_de_fora(self):
        self.assertIsNone(self._intervalo_curto('08:00', '17:00'))

    def test_marcacao_impar_fica_de_fora(self):
        self.assertIsNone(self._intervalo_curto('06:00', '12:30', '12:40', '16:00', '16:10'))


class AjusteTurnoNoturnoTests(TestCase):
    """
    Ajuste manual em turno que vira a noite (12x36 18:00-06:00): o ajuste entra no
    horário em que a batida aconteceu e é encaixado no turno como as batidas reais.
    """

    def setUp(self):
        self.equipamento = _criar_equipamento()
        self.funcionario = _criar_funcionario(cpf='55555555555')
        self.dia = date(2026, 8, 3)  # início do ciclo: dia de trabalho
        self.dia_seguinte = self.dia + timedelta(days=1)  # folga do 12x36
        FuncionarioJornada.objects.create(
            funcionario=self.funcionario, data_inicio=self.dia, horario=_criar_horario_12x36_noturno(4501),
        )

    def test_batida_que_faltou_com_data_do_dia_seguinte_fica_no_turno(self):
        _bater_ponto(self.funcionario, self.equipamento, self.dia, '18:00', '22:00', '23:00')  # esqueceu a saída
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=self.dia_seguinte, tipo_evento='saida',
            horario_ajustado='06:00:00', motivo='Esqueceu de bater a saída.', status='aprovado',
        )

        turno, seguinte = calcular_periodo(self.funcionario, self.dia, self.dia_seguinte)['dias']

        self.assertEqual(len(turno['marcacoes']), 4)
        self.assertFalse(turno['marcacao_impar'])
        self.assertEqual(turno['marcacoes'][-1].momento, _momento_local(self.dia_seguinte, '06:00'))
        self.assertTrue(turno['marcacoes'][-1].manual)
        self.assertEqual(turno['segundos_trabalhados'], 11 * 3600)
        self.assertEqual(seguinte['marcacoes'], [])

    def test_correcao_usa_a_data_lancada(self):
        _bater_ponto(self.funcionario, self.equipamento, self.dia, '18:00', '22:00', '23:00')
        saida_errada = _bater_ponto(self.funcionario, self.equipamento, self.dia_seguinte, '06:03')[0]
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=self.dia_seguinte, tipo_evento='saida',
            horario_ajustado='06:10:00', marcacao_original=saida_errada,
            motivo='A saída correta é 06:10.', status='aprovado',
        )

        turno = calcular_periodo(self.funcionario, self.dia, self.dia)['dias'][0]

        self.assertEqual(len(turno['marcacoes']), 4)  # a original (06:03) saiu da conta
        ultimo = turno['marcacoes'][-1]
        self.assertEqual(ultimo.momento, _momento_local(self.dia_seguinte, '06:10'))
        self.assertTrue(ultimo.manual)

    def test_correcao_que_passa_da_meia_noite_usa_a_data_lancada(self):
        # Relógio registrou a volta do intervalo às 23:58 e a correta foi 00:05 do dia seguinte.
        volta_errada = _bater_ponto(self.funcionario, self.equipamento, self.dia, '18:00', '22:00', '23:58')[-1]
        _bater_ponto(self.funcionario, self.equipamento, self.dia_seguinte, '06:00')
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=self.dia_seguinte, tipo_evento='entrada',
            horario_ajustado='00:05:00', marcacao_original=volta_errada,
            motivo='A volta do intervalo foi 00:05.', status='aprovado',
        )

        turno = calcular_periodo(self.funcionario, self.dia, self.dia)['dias'][0]

        self.assertEqual([e.momento for e in turno['marcacoes']], [
            _momento_local(self.dia, '18:00'), _momento_local(self.dia, '22:00'),
            _momento_local(self.dia_seguinte, '00:05'), _momento_local(self.dia_seguinte, '06:00'),
        ])

    def test_correcao_de_mais_de_12h_fica_na_data_e_hora_lancadas(self):
        # Entrada registrada às 05:00, a correta foi 18:00 do mesmo dia (13h depois).
        # Nada é deduzido: vale o dia e a hora que foram lançados.
        entrada_errada = _bater_ponto(self.funcionario, self.equipamento, self.dia, '05:00', '22:00', '23:00')[0]
        _bater_ponto(self.funcionario, self.equipamento, self.dia_seguinte, '06:00')
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=self.dia, tipo_evento='entrada',
            horario_ajustado='18:00:00', marcacao_original=entrada_errada,
            motivo='A entrada foi 18:00.', status='aprovado',
        )

        turno = calcular_periodo(self.funcionario, self.dia, self.dia)['dias'][0]

        self.assertEqual([e.momento for e in turno['marcacoes']], [
            _momento_local(self.dia, '18:00'), _momento_local(self.dia, '22:00'),
            _momento_local(self.dia, '23:00'), _momento_local(self.dia_seguinte, '06:00'),
        ])
        self.assertFalse(turno['marcacao_impar'])
        self.assertEqual(turno['segundos_trabalhados'], 11 * 3600)

    def test_ajuste_pendente_nao_conta(self):
        _bater_ponto(self.funcionario, self.equipamento, self.dia, '18:00', '22:00', '23:00')
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=self.dia_seguinte, tipo_evento='saida',
            horario_ajustado='06:00:00', motivo='Pendente.', status='pendente',
        )

        turno = calcular_periodo(self.funcionario, self.dia, self.dia)['dias'][0]

        self.assertTrue(turno['marcacao_impar'])
        self.assertEqual(len(turno['marcacoes']), 3)


class AjustarMarcacaoViewTests(TestCase):
    """Tela de lançar/corrigir uma batida manualmente, a partir do espelho de Cálculo."""

    def setUp(self):
        setor = Setor.objects.create(nome='RH')
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9999', cargo='RH', setor=setor, cpf='22222222222')
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.login(username='rhteste', password='senha123')
        self.equipamento = _criar_equipamento()
        self.funcionario = _criar_funcionario(cpf='55555555555')

    def test_lanca_ajuste_e_registra_usuario_e_motivo(self):
        dia = date(2026, 8, 3)

        resposta = self.client.post(
            reverse('ponto:ajustar_marcacao', args=[self.funcionario.id, dia.isoformat()]),
            {'data': dia.isoformat(), 'tipo_evento': 'entrada', 'horario_ajustado': '08:00', 'motivo': 'Esqueceu de bater.'},
        )

        self.assertRedirects(resposta, reverse('ponto:listar_calculo') + f'?funcionario={self.funcionario.id}')
        ajuste = AjustePonto.objects.get(funcionario=self.funcionario, data=dia)
        self.assertEqual(ajuste.tipo_evento, 'entrada')
        self.assertEqual(str(ajuste.horario_ajustado), '08:00:00')
        self.assertEqual(ajuste.usuario.username, 'rhteste')
        self.assertIsNone(ajuste.marcacao_original)

    def test_lanca_ajuste_com_correcao_de_marcacao_existente(self):
        dia = date(2026, 8, 3)
        marcacoes = _bater_ponto(self.funcionario, self.equipamento, dia, '16:00')
        marcacao_errada = marcacoes[0]

        self.client.post(
            reverse('ponto:ajustar_marcacao', args=[self.funcionario.id, dia.isoformat()]),
            {
                'data': dia.isoformat(), 'tipo_evento': 'saida', 'horario_ajustado': '17:00', 'motivo': 'Corrigindo.',
                'marcacao_original': marcacao_errada.id,
            },
        )

        ajuste = AjustePonto.objects.get(funcionario=self.funcionario, data=dia)
        self.assertEqual(ajuste.marcacao_original, marcacao_errada)

    def test_volta_pra_url_informada_em_voltar(self):
        dia = date(2026, 8, 3)

        resposta = self.client.post(
            reverse('ponto:ajustar_marcacao', args=[self.funcionario.id, dia.isoformat()]),
            {
                'data': dia.isoformat(), 'tipo_evento': 'entrada', 'horario_ajustado': '08:00', 'motivo': 'Teste.',
                'voltar': f'/funcionarios/{self.funcionario.id}/',
            },
        )

        self.assertRedirects(resposta, f'/funcionarios/{self.funcionario.id}/', fetch_redirect_response=False)

    def test_form_ja_vem_preenchido_com_o_horario_da_marcacao_a_corrigir(self):
        dia = date(2026, 8, 3)
        marcacoes = _bater_ponto(self.funcionario, self.equipamento, dia, '16:00')

        resposta = self.client.get(
            reverse('ponto:ajustar_marcacao', args=[self.funcionario.id, dia.isoformat()])
            + f'?tipo=saida&marcacao_original={marcacoes[0].id}'
        )

        self.assertEqual(resposta.context['form'].initial['horario_ajustado'], marcacoes[0].momento.time())

    def test_ajuste_lancado_pelo_rh_ja_sai_aprovado(self):
        dia = date(2026, 8, 3)

        self.client.post(
            reverse('ponto:ajustar_marcacao', args=[self.funcionario.id, dia.isoformat()]),
            {'data': dia.isoformat(), 'tipo_evento': 'entrada', 'horario_ajustado': '08:00', 'motivo': 'Esqueceu de bater.'},
        )

        ajuste = AjustePonto.objects.get(funcionario=self.funcionario, data=dia)
        self.assertEqual(ajuste.status, 'aprovado')
        self.assertEqual(ajuste.solicitado_por.username, 'rhteste')
        self.assertEqual(ajuste.analisado_por.username, 'rhteste')
        self.assertIsNotNone(ajuste.analisado_em)

    def test_bloqueia_lancar_direto_se_ja_existe_aprovado_pro_mesmo_slot(self):
        dia = date(2026, 8, 3)
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=dia, tipo_evento='entrada',
            horario_ajustado='08:00:00', motivo='Já aprovado.', status='aprovado',
        )

        resposta = self.client.post(
            reverse('ponto:ajustar_marcacao', args=[self.funcionario.id, dia.isoformat()]),
            {'data': dia.isoformat(), 'tipo_evento': 'entrada', 'horario_ajustado': '08:10', 'motivo': 'Tentando de novo.'},
        )

        self.assertRedirects(resposta, reverse('ponto:listar_calculo') + f'?funcionario={self.funcionario.id}')
        self.assertEqual(
            AjustePonto.objects.filter(funcionario=self.funcionario, data=dia, tipo_evento='entrada').count(), 1
        )


    def test_grava_a_data_informada_no_formulario(self):
        dia = date(2026, 8, 3)  # linha do espelho (dia em que o turno noturno começou)

        resposta = self.client.post(
            reverse('ponto:ajustar_marcacao', args=[self.funcionario.id, dia.isoformat()]),
            {
                'data': (dia + timedelta(days=1)).isoformat(), 'tipo_evento': 'saida',
                'horario_ajustado': '06:00', 'motivo': 'Esqueceu a saída.',
            },
        )

        ajuste = AjustePonto.objects.get(funcionario=self.funcionario)
        self.assertEqual(ajuste.data, dia + timedelta(days=1))
        mensagens = [str(m) for m in get_messages(resposta.wsgi_request)]
        self.assertIn('registrado: Saída às 06:00 de 04/08/2026.', mensagens[0])

    def test_batida_que_faltou_vem_com_a_data_da_linha(self):
        dia = date(2026, 8, 3)

        resposta = self.client.get(
            reverse('ponto:ajustar_marcacao', args=[self.funcionario.id, dia.isoformat()]) + '?tipo=saida'
        )

        self.assertEqual(resposta.context['form'].initial['data'], dia)
        self.assertContains(resposta, 'value="2026-08-03"')  # formato que o <input type="date"> entende

    def test_sem_data_nao_salva(self):
        dia = date(2026, 8, 3)

        resposta = self.client.post(
            reverse('ponto:ajustar_marcacao', args=[self.funcionario.id, dia.isoformat()]),
            {'data': '', 'tipo_evento': 'saida', 'horario_ajustado': '06:00', 'motivo': 'Sem data.'},
        )

        self.assertEqual(resposta.status_code, 200)  # formulário volta com erro
        self.assertFalse(AjustePonto.objects.filter(funcionario=self.funcionario).exists())

    def test_correcao_vem_com_data_e_hora_da_batida_original(self):
        dia = date(2026, 8, 3)  # linha do turno; a saída foi batida no dia seguinte
        saida = _bater_ponto(self.funcionario, self.equipamento, dia + timedelta(days=1), '06:03')[0]

        resposta = self.client.get(
            reverse('ponto:ajustar_marcacao', args=[self.funcionario.id, dia.isoformat()])
            + f'?tipo=saida&marcacao_original={saida.id}'
        )

        inicial = resposta.context['form'].initial
        self.assertEqual(inicial['data'], dia + timedelta(days=1))
        self.assertEqual(inicial['horario_ajustado'], saida.momento.time())
        self.assertContains(resposta, 'value="2026-08-04"')

    def test_duplicidade_compara_pela_data_real_da_batida(self):
        dia = date(2026, 8, 3)
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=dia, tipo_evento='saida',
            horario_ajustado='17:00:00', motivo='Já aprovado.', status='aprovado',
        )

        # Mesma linha do espelho e mesmo tipo, mas a batida é do dia seguinte: não é duplicidade.
        self.client.post(
            reverse('ponto:ajustar_marcacao', args=[self.funcionario.id, dia.isoformat()]),
            {
                'data': (dia + timedelta(days=1)).isoformat(), 'tipo_evento': 'saida',
                'horario_ajustado': '06:00', 'motivo': 'Saída do turno.',
            },
        )

        self.assertEqual(
            sorted(AjustePonto.objects.filter(funcionario=self.funcionario, tipo_evento='saida').values_list('data', flat=True)),
            [dia, dia + timedelta(days=1)],
        )

    def test_duplicidade_no_dia_seguinte_continua_bloqueada(self):
        dia = date(2026, 8, 3)
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=dia + timedelta(days=1), tipo_evento='saida',
            horario_ajustado='06:00:00', motivo='Já aprovado.', status='aprovado',
        )

        resposta = self.client.post(
            reverse('ponto:ajustar_marcacao', args=[self.funcionario.id, dia.isoformat()]),
            {
                'data': (dia + timedelta(days=1)).isoformat(), 'tipo_evento': 'saida',
                'horario_ajustado': '06:05', 'motivo': 'De novo.',
            },
        )

        self.assertEqual(AjustePonto.objects.filter(funcionario=self.funcionario, tipo_evento='saida').count(), 1)
        mensagens = [str(m) for m in get_messages(resposta.wsgi_request)]
        self.assertIn('em 04/08/2026 (Saída)', mensagens[0])


class AtribuirJornadaViewTests(TestCase):
    """"Nova jornada a partir de" na tela do espelho: muda o horário só daquela data em diante."""

    def setUp(self):
        setor = Setor.objects.create(nome='RH')
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9999', cargo='RH', setor=setor, cpf='22222222222')
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.login(username='rhteste', password='senha123')
        self.funcionario = _criar_funcionario(cpf='55555555555')
        self.horario_44h = _criar_horario_util(4601)
        self.horario_40h = _criar_horario_util(4602, saida2='16:00')
        self.url = reverse('ponto:atribuir_jornada', args=[self.funcionario.id])

    def test_define_jornada_a_partir_da_data(self):
        resposta = self.client.post(self.url, {'data_inicio': '2026-03-01', 'horario': self.horario_40h.id})

        jornada = FuncionarioJornada.objects.get(funcionario=self.funcionario)
        self.assertEqual(jornada.data_inicio, date(2026, 3, 1))
        self.assertEqual(jornada.horario, self.horario_40h)
        mensagens = [str(m) for m in get_messages(resposta.wsgi_request)]
        self.assertIn('a partir de 01/03/2026', mensagens[0])

    def test_mesma_data_troca_o_horario_em_vez_de_dar_erro(self):
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 1, 1), horario=self.horario_44h)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 3, 1), horario=self.horario_44h)

        # Escolheu o horário errado pra 01/03 e cadastra de novo, na mesma data, com o certo.
        resposta = self.client.post(self.url, {'data_inicio': '2026-03-01', 'horario': self.horario_40h.id})

        self.assertEqual(resposta.status_code, 302)
        jornadas = FuncionarioJornada.objects.filter(funcionario=self.funcionario).order_by('data_inicio')
        self.assertEqual(
            [(j.data_inicio, j.horario) for j in jornadas],
            [(date(2026, 1, 1), self.horario_44h), (date(2026, 3, 1), self.horario_40h)],  # a de janeiro não muda
        )
        mensagens = [str(m) for m in get_messages(resposta.wsgi_request)]
        self.assertIn(f'trocado de {self.horario_44h} para {self.horario_40h}', mensagens[0])

    def test_mesma_data_e_mesmo_horario_nao_muda_nada(self):
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 3, 1), horario=self.horario_40h)

        resposta = self.client.post(self.url, {'data_inicio': '2026-03-01', 'horario': self.horario_40h.id})

        self.assertEqual(FuncionarioJornada.objects.filter(funcionario=self.funcionario).count(), 1)
        mensagens = [str(m) for m in get_messages(resposta.wsgi_request)]
        self.assertIn('já era', mensagens[0])


class EspelhoCalculoViewTests(TestCase):
    """Espelho do RH: marca de dia seguinte nas batidas do turno noturno e rótulo de feriado."""

    def setUp(self):
        setor = Setor.objects.create(nome='RH')
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9999', cargo='RH', setor=setor, cpf='22222222222')
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.login(username='rhteste', password='senha123')
        self.equipamento = _criar_equipamento()
        self.funcionario = _criar_funcionario(cpf='55555555555')

    def _espelho(self, dia):
        return self.client.get(
            reverse('ponto:listar_calculo')
            + f'?funcionario={self.funcionario.id}&data_inicio={dia.isoformat()}&data_fim={dia.isoformat()}'
        )

    def test_saida_depois_da_meia_noite_ganha_marca_de_dia_seguinte(self):
        dia = date(2026, 8, 3)
        FuncionarioJornada.objects.create(
            funcionario=self.funcionario, data_inicio=dia, horario=_criar_horario_12x36_noturno(4503),
        )
        _bater_ponto(self.funcionario, self.equipamento, dia, '18:00', '22:00', '23:00')
        saida = _bater_ponto(self.funcionario, self.equipamento, dia + timedelta(days=1), '06:00')[0]

        resposta = self._espelho(dia)

        self.assertContains(resposta, '>+1</sup>', count=1)
        self.assertNotContains(resposta, '>−1</sup>')
        # O lápis da batida leva a marcação a corrigir, pra virar correção e não batida a mais.
        self.assertContains(resposta, f'marcacao_original={saida.id}')

    def test_dia_de_feriado_aparece_como_feriado(self):
        horario = _criar_horario_util(4504)
        horario.considerar_feriados = True
        horario.save()
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        feriado = date(2026, 9, 7)  # segunda-feira
        Feriado.objects.create(data=feriado, descricao='Independência')

        resposta = self._espelho(feriado)

        self.assertContains(resposta, 'title="Feriado"')
        self.assertEqual(resposta.context['resultado']['dias'][0]['segundos_falta_dia'], 0)

    def test_dia_falta_e_valores_em_reais(self):
        self.funcionario.salario = Decimal('3000.00')
        self.funcionario.save()
        FuncionarioJornada.objects.create(
            funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=_criar_horario_util(4505),
        )

        resposta = self._espelho(date(2026, 8, 3))  # segunda-feira sem nenhuma batida

        self.assertContains(resposta, 'title="Dia falta"')
        self.assertContains(resposta, '>Perdida<')  # assiduidade
        self.assertContains(resposta, 'A descontar: <strong>R$ 200,00</strong>')  # dia + DSR, 1/30 cada

    def test_turno_noturno_mostra_adicional_noturno(self):
        self.funcionario.salario = Decimal('2200.00')
        self.funcionario.save()
        dia = date(2026, 8, 3)
        FuncionarioJornada.objects.create(
            funcionario=self.funcionario, data_inicio=dia, horario=_criar_horario_12x36_noturno(4507),
        )
        _bater_ponto(self.funcionario, self.equipamento, dia, '18:00', '22:00', '23:00')
        _bater_ponto(self.funcionario, self.equipamento, dia + timedelta(days=1), '06:00')

        resposta = self._espelho(dia)

        self.assertContains(resposta, '<div class="totais-grid__valor">06:00</div>')  # 6h noturnas de relógio
        self.assertContains(resposta, '06:51')  # as mesmas 6h, convertidas na hora de 52min30s
        self.assertContains(resposta, 'R$ 13,71')  # 10 x 20% x 6,857h

    def test_sem_salario_nao_mostra_valores(self):
        FuncionarioJornada.objects.create(
            funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=_criar_horario_util(4506),
        )

        resposta = self._espelho(date(2026, 8, 3))

        self.assertContains(resposta, 'Sem salário cadastrado')
        self.assertNotContains(resposta, 'A descontar:')


class MeuPontoViewTests(TestCase):
    """Tela 'Meu Ponto' do colaborador -- autoatendimento, sem @papel_required."""

    def setUp(self):
        setor = Setor.objects.create(nome='TI')
        self.funcionario = Funcionario.objects.create(
            nome='Ana Silva', matricula='2001', cargo='Analista', setor=setor, cpf='33333333333',
        )
        user = User.objects.create_user(username='anasilva', password='senha123')
        Usuario.objects.create(user=user, funcionario=self.funcionario, papel='colaborador')
        self.client.login(username='anasilva', password='senha123')

        self.equipamento = _criar_equipamento()
        self.outro_funcionario = _criar_funcionario(cpf='44444444444', matricula='2002')
        horario = _criar_horario_util(4101)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        FuncionarioJornada.objects.create(funcionario=self.outro_funcionario, data_inicio=date(2026, 8, 1), horario=horario)

    def test_mostra_so_as_proprias_batidas(self):
        segunda = date(2026, 8, 3)
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:00', '12:00', '13:00', '17:00')
        _bater_ponto(self.outro_funcionario, self.equipamento, segunda, '09:00', '12:00', '13:00', '19:00')

        resposta = self.client.get(
            reverse('ponto:meu_ponto') + f'?data_inicio={segunda.isoformat()}&data_fim={segunda.isoformat()}'
        )

        self.assertEqual(resposta.status_code, 200)
        dia = resposta.context['resultado']['dias'][0]
        self.assertEqual(dia['segundos_trabalhados'], 8 * 3600)  # a própria jornada (08-17), não a de 10h do outro

    def test_dia_de_hoje_com_marcacao_impar_aparece_como_em_andamento(self):
        hoje = timezone.now().date()
        FuncionarioJornada.objects.filter(funcionario=self.funcionario).update(data_inicio=hoje - timedelta(days=60))
        _bater_ponto(self.funcionario, self.equipamento, hoje, '08:00')  # só a entrada -- o dia não terminou

        resposta = self.client.get(reverse('ponto:meu_ponto'))

        dia_hoje = next(d for d in resposta.context['resultado']['dias'] if d['data'] == hoje)
        self.assertEqual(dia_hoje['situacao_texto'], 'Em andamento')
        self.assertFalse(dia_hoje['pode_solicitar'])

    def test_atraso_dentro_da_tolerancia_aparece_como_ok(self):
        segunda = date(2026, 8, 3)
        _bater_ponto(self.funcionario, self.equipamento, segunda, '08:00', '12:00', '13:04', '17:00')  # 4 min a menos

        resposta = self.client.get(
            reverse('ponto:meu_ponto') + f'?data_inicio={segunda.isoformat()}&data_fim={segunda.isoformat()}'
        )

        dia = resposta.context['resultado']['dias'][0]
        self.assertEqual(dia['situacao_texto'], 'OK')
        self.assertFalse(dia['pode_solicitar'])
        self.assertNotContains(resposta, 'Falta/atraso')

    def test_sem_perfil_nao_quebra(self):
        User.objects.create_user(username='semperfil', password='senha123')
        self.client.login(username='semperfil', password='senha123')

        resposta = self.client.get(reverse('ponto:meu_ponto'))

        self.assertEqual(resposta.status_code, 200)
        self.assertTemplateUsed(resposta, 'funcionarios/sem_perfil.html')


    def test_saida_depois_da_meia_noite_ganha_marca_de_dia_seguinte(self):
        dia = date(2026, 8, 10)  # a partir daqui vale a jornada noturna (a diurna do setUp fica pra trás)
        FuncionarioJornada.objects.create(
            funcionario=self.funcionario, data_inicio=dia, horario=_criar_horario_12x36_noturno(4502),
        )
        _bater_ponto(self.funcionario, self.equipamento, dia, '18:00', '22:00', '23:00')
        _bater_ponto(self.funcionario, self.equipamento, dia + timedelta(days=1), '06:00')

        resposta = self.client.get(
            reverse('ponto:meu_ponto') + f'?data_inicio={dia.isoformat()}&data_fim={dia.isoformat()}'
        )

        self.assertContains(resposta, '>+1</sup>', count=1)


class PontoEquipeViewTests(TestCase):
    """Gestor tem cargo de confiança e não bate ponto -- vê o ponto da equipe no lugar do próprio."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='Produção')
        self.gestor = Funcionario.objects.create(
            nome='Gil Gestor', matricula='3001', cargo='Gerente', setor=self.setor, cpf='77711111111',
        )
        self.setor.gestor = self.gestor
        self.setor.save()
        user = User.objects.create_user(username='gilgestor', password='senha123')
        Usuario.objects.create(user=user, funcionario=self.gestor, papel='gestor')
        self.client.login(username='gilgestor', password='senha123')

        self.equipamento = _criar_equipamento()
        horario = _criar_horario_util(4201)
        self.membro = Funcionario.objects.create(
            nome='Bia Operadora', matricula='3002', cargo='Operadora', setor=self.setor, cpf='77722222222',
        )
        FuncionarioJornada.objects.create(funcionario=self.membro, data_inicio=date(2026, 8, 1), horario=horario)
        self.de_fora = Funcionario.objects.create(
            nome='Caio Outro Setor', matricula='3003', cargo='Analista',
            setor=Setor.objects.create(nome='Financeiro'), cpf='77733333333',
        )

    def test_meu_ponto_redireciona_gestor_para_equipe(self):
        for nome in ('ponto:meu_ponto', 'ponto:meus_ajustes', 'ponto:solicitar_ajuste_ponto'):
            self.assertRedirects(self.client.get(reverse(nome)), reverse('ponto:ponto_equipe'))

    def test_lista_batidas_so_da_equipe_e_sem_o_gestor(self):
        segunda = date(2026, 8, 3)
        _bater_ponto(self.membro, self.equipamento, segunda, '08:00', '12:00', '13:00', '17:00')

        resposta = self.client.get(reverse('ponto:ponto_equipe') + f'?data={segunda.isoformat()}')

        self.assertEqual(resposta.status_code, 200)
        funcionarios = [linha['funcionario'] for linha in resposta.context['linhas']]
        self.assertEqual(funcionarios, [self.membro])
        self.assertEqual(resposta.context['linhas'][0]['situacao_texto'], 'OK')
        self.assertNotContains(resposta, 'Meu Ponto')

    def test_espelho_de_funcionario_de_outro_setor_da_404(self):
        self.assertEqual(
            self.client.get(reverse('ponto:ponto_equipe_funcionario', args=[self.membro.id])).status_code, 200,
        )
        self.assertEqual(
            self.client.get(reverse('ponto:ponto_equipe_funcionario', args=[self.de_fora.id])).status_code, 404,
        )

    def test_colaborador_nao_acessa_ponto_da_equipe(self):
        user = User.objects.create_user(username='biaoperadora', password='senha123')
        Usuario.objects.create(user=user, funcionario=self.membro, papel='colaborador')
        self.client.login(username='biaoperadora', password='senha123')

        self.assertRedirects(self.client.get(reverse('ponto:ponto_equipe')), reverse('dashboard'), fetch_redirect_response=False)


class SolicitarAjustePontoViewTests(TestCase):
    def setUp(self):
        setor = Setor.objects.create(nome='TI')
        self.funcionario = Funcionario.objects.create(
            nome='Ana Silva', matricula='2101', cargo='Analista', setor=setor, cpf='55511111111',
        )
        self.usuario = User.objects.create_user(username='anasolicita', password='senha123')
        Usuario.objects.create(user=self.usuario, funcionario=self.funcionario, papel='colaborador')
        self.client.login(username='anasolicita', password='senha123')
        self.equipamento = _criar_equipamento()

    def test_cria_ajuste_pendente_com_solicitado_por(self):
        dia = date(2026, 8, 3)

        resposta = self.client.post(reverse('ponto:solicitar_ajuste_ponto'), {
            'data': dia.isoformat(), 'tipo_evento': 'entrada', 'horario_ajustado': '08:00',
            'marcacao_original': '', 'motivo': 'Esqueci de bater.',
        })

        self.assertRedirects(resposta, reverse('ponto:meus_ajustes'))
        ajuste = AjustePonto.objects.get(funcionario=self.funcionario, data=dia)
        self.assertEqual(ajuste.status, 'pendente')
        self.assertEqual(ajuste.solicitado_por, self.usuario)
        self.assertEqual(ajuste.usuario, self.usuario)
        self.assertIsNone(ajuste.analisado_por)

    def test_bloqueia_segunda_solicitacao_pendente_pro_mesmo_slot(self):
        dia = date(2026, 8, 3)
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=dia, tipo_evento='entrada', horario_ajustado='08:00:00',
            motivo='Primeira.', status='pendente', solicitado_por=self.usuario,
        )

        resposta = self.client.post(reverse('ponto:solicitar_ajuste_ponto'), {
            'data': dia.isoformat(), 'tipo_evento': 'entrada', 'horario_ajustado': '08:05',
            'marcacao_original': '', 'motivo': 'Segunda tentativa.',
        })

        self.assertEqual(resposta.status_code, 200)  # não redireciona -- form volta com erro
        self.assertEqual(
            AjustePonto.objects.filter(funcionario=self.funcionario, data=dia, tipo_evento='entrada').count(), 1
        )

    def test_marcacao_original_so_oferece_batidas_do_proprio_funcionario_perto_da_data(self):
        dia = date(2026, 8, 3)
        outro_funcionario = _criar_funcionario(cpf='55522222222', matricula='2102')
        vespera = _bater_ponto(self.funcionario, self.equipamento, dia - timedelta(days=1), '23:58')
        do_dia = _bater_ponto(self.funcionario, self.equipamento, dia, '16:00')
        seguinte = _bater_ponto(self.funcionario, self.equipamento, dia + timedelta(days=1), '09:00')
        _bater_ponto(outro_funcionario, self.equipamento, dia, '16:30')
        _bater_ponto(self.funcionario, self.equipamento, dia + timedelta(days=2), '09:00')  # longe demais

        resposta = self.client.get(reverse('ponto:solicitar_ajuste_ponto') + f'?data={dia.isoformat()}')

        queryset = resposta.context['form'].fields['marcacao_original'].queryset
        self.assertEqual(list(queryset), vespera + do_dia + seguinte)
        self.assertContains(resposta, '02/08 23:58')  # a opção mostra a data, já que são três dias

    def test_marcacao_longe_da_data_e_rejeitada_na_validacao(self):
        dia = date(2026, 8, 3)
        marcacao_longe = _bater_ponto(self.funcionario, self.equipamento, dia + timedelta(days=2), '09:00')[0]

        resposta = self.client.post(reverse('ponto:solicitar_ajuste_ponto'), {
            'data': dia.isoformat(), 'tipo_evento': 'entrada', 'horario_ajustado': '08:00',
            'marcacao_original': marcacao_longe.id, 'motivo': 'Referenciando batida de dois dias depois.',
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertFalse(AjustePonto.objects.filter(funcionario=self.funcionario, data=dia).exists())

    def test_corrige_batida_da_vespera_com_a_data_real(self):
        # Volta do intervalo registrada 23:58 de 03/08; a correta foi 00:05 de 04/08.
        volta_errada = _bater_ponto(self.funcionario, self.equipamento, date(2026, 8, 3), '23:58')[0]

        resposta = self.client.post(reverse('ponto:solicitar_ajuste_ponto'), {
            'data': '2026-08-04', 'tipo_evento': 'entrada', 'horario_ajustado': '00:05',
            'marcacao_original': volta_errada.id, 'motivo': 'Voltei 00:05.',
        })

        self.assertRedirects(resposta, reverse('ponto:meus_ajustes'))
        ajuste = AjustePonto.objects.get(funcionario=self.funcionario)
        self.assertEqual(ajuste.data, date(2026, 8, 4))
        self.assertEqual(ajuste.marcacao_original, volta_errada)


class MeusAjustesViewTests(TestCase):
    def setUp(self):
        setor = Setor.objects.create(nome='TI')
        self.funcionario = Funcionario.objects.create(
            nome='Ana Silva', matricula='2201', cargo='Analista', setor=setor, cpf='55533333333',
        )
        user = User.objects.create_user(username='anavelista', password='senha123')
        Usuario.objects.create(user=user, funcionario=self.funcionario, papel='colaborador')
        self.client.login(username='anavelista', password='senha123')

    def test_rejeitado_continua_visivel_com_observacao_do_rh(self):
        outro_funcionario = _criar_funcionario(cpf='55544444444', matricula='2202')
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=date(2026, 8, 3), tipo_evento='entrada', horario_ajustado='08:00:00',
            motivo='Meu pedido.', status='rejeitado', observacao_rh='Bateu no relógio certinho, sem erro.',
        )
        AjustePonto.objects.create(
            funcionario=outro_funcionario, data=date(2026, 8, 3), tipo_evento='entrada', horario_ajustado='08:00:00',
            motivo='Pedido de outro funcionário.', status='pendente',
        )

        resposta = self.client.get(reverse('ponto:meus_ajustes'))

        self.assertEqual(resposta.status_code, 200)
        ajustes = list(resposta.context['ajustes'])
        self.assertEqual(len(ajustes), 1)  # só o próprio, não o de outro_funcionario
        self.assertContains(resposta, 'Bateu no relógio certinho, sem erro.')


class AjustesPendentesViewTests(TestCase):
    def setUp(self):
        setor = Setor.objects.create(nome='RH')
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9998', cargo='RH', setor=setor, cpf='66666666666')
        user = User.objects.create_user(username='rhpendentes', password='senha123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.login(username='rhpendentes', password='senha123')
        self.funcionario = _criar_funcionario(cpf='77777777777')

    def test_lista_so_pendentes(self):
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=date(2026, 8, 3), tipo_evento='entrada',
            horario_ajustado='08:00:00', motivo='Pendente.', status='pendente',
        )
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=date(2026, 8, 4), tipo_evento='entrada',
            horario_ajustado='08:00:00', motivo='Já aprovado.', status='aprovado',
        )

        resposta = self.client.get(reverse('ponto:ajustes_pendentes'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(len(resposta.context['ajustes']), 1)

    def test_colaborador_nao_acessa(self):
        colaborador_user = User.objects.create_user(username='colabpendentes', password='senha123')
        Usuario.objects.create(user=colaborador_user, funcionario=self.funcionario, papel='colaborador')
        self.client.login(username='colabpendentes', password='senha123')

        resposta = self.client.get(reverse('ponto:ajustes_pendentes'))

        self.assertRedirects(resposta, reverse('dashboard'))


class AprovarRejeitarAjusteViewTests(TestCase):
    def setUp(self):
        setor = Setor.objects.create(nome='RH')
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9997', cargo='RH', setor=setor, cpf='88888888888')
        self.rh_user = User.objects.create_user(username='rhaprova', password='senha123')
        Usuario.objects.create(user=self.rh_user, funcionario=rh, papel='rh')
        self.client.login(username='rhaprova', password='senha123')
        self.funcionario = _criar_funcionario(cpf='99999999999')

    def test_aprovar_seta_status_e_analise(self):
        ajuste = AjustePonto.objects.create(
            funcionario=self.funcionario, data=date(2026, 8, 3), tipo_evento='entrada',
            horario_ajustado='08:00:00', motivo='Esqueci.', status='pendente',
        )

        resposta = self.client.post(reverse('ponto:aprovar_ajuste', args=[ajuste.id]))

        self.assertRedirects(resposta, reverse('ponto:ajustes_pendentes'))
        ajuste.refresh_from_db()
        self.assertEqual(ajuste.status, 'aprovado')
        self.assertEqual(ajuste.analisado_por, self.rh_user)
        self.assertIsNotNone(ajuste.analisado_em)

    def test_aprovar_bloqueado_se_ja_existe_aprovado_pro_mesmo_slot(self):
        AjustePonto.objects.create(
            funcionario=self.funcionario, data=date(2026, 8, 3), tipo_evento='entrada',
            horario_ajustado='08:00:00', motivo='Já aprovado antes.', status='aprovado',
        )
        pendente = AjustePonto.objects.create(
            funcionario=self.funcionario, data=date(2026, 8, 3), tipo_evento='entrada',
            horario_ajustado='08:10:00', motivo='Pedido duplicado.', status='pendente',
        )

        self.client.post(reverse('ponto:aprovar_ajuste', args=[pendente.id]))

        pendente.refresh_from_db()
        self.assertEqual(pendente.status, 'pendente')  # não aprova por cima do que já está aprovado

    def test_rejeitar_exige_observacao(self):
        ajuste = AjustePonto.objects.create(
            funcionario=self.funcionario, data=date(2026, 8, 3), tipo_evento='entrada',
            horario_ajustado='08:00:00', motivo='Esqueci.', status='pendente',
        )

        self.client.post(reverse('ponto:rejeitar_ajuste', args=[ajuste.id]), {'observacao_rh': ''})
        ajuste.refresh_from_db()
        self.assertEqual(ajuste.status, 'pendente')  # sem motivo, não rejeita

        resposta = self.client.post(
            reverse('ponto:rejeitar_ajuste', args=[ajuste.id]), {'observacao_rh': 'Bateu certo, sem erro.'}
        )

        self.assertRedirects(resposta, reverse('ponto:ajustes_pendentes'))
        ajuste.refresh_from_db()
        self.assertEqual(ajuste.status, 'rejeitado')
        self.assertEqual(ajuste.observacao_rh, 'Bateu certo, sem erro.')
        self.assertEqual(ajuste.analisado_por, self.rh_user)


class GestorOperaPontoDaEquipeTests(TestCase):
    """O gestor corrige o ponto e analisa os pedidos de ajuste como o RH, mas só da equipe dele."""

    def setUp(self):
        self.producao = Setor.objects.create(nome='Produção')
        self.envase = Setor.objects.create(nome='Envase')
        self.gestor = Funcionario.objects.create(
            nome='Gil Gestor', matricula='3101', cargo='Gerente', setor=self.producao, cpf='78811111111',
        )
        self.producao.gestor = self.gestor
        self.producao.save()
        self.envase.gestor = self.gestor  # responde também pelo Envase, sem estar lotado nele
        self.envase.save()
        self.gestor_user = User.objects.create_user(username='gilgestor', password='senha123')
        Usuario.objects.create(user=self.gestor_user, funcionario=self.gestor, papel='gestor')
        self.client.login(username='gilgestor', password='senha123')

        self.equipamento = _criar_equipamento()
        horario = _criar_horario_util(4301)
        self.bia = Funcionario.objects.create(
            nome='Bia Operadora', matricula='3102', cargo='Operadora', setor=self.producao, cpf='78822222222',
            salario=Decimal('3000.00'),
        )
        self.davi = Funcionario.objects.create(
            nome='Davi Envase', matricula='3103', cargo='Operador', setor=self.envase, cpf='78833333333',
        )
        for funcionario in (self.bia, self.davi):
            FuncionarioJornada.objects.create(funcionario=funcionario, data_inicio=date(2026, 8, 1), horario=horario)
        outra_gestora = Funcionario.objects.create(
            nome='Eva Supervisora', matricula='3104', cargo='Supervisora', setor=self.producao, cpf='78844444444',
        )
        Usuario.objects.create(
            user=User.objects.create_user(username='evagestora', password='senha123'), funcionario=outra_gestora, papel='gestor',
        )
        self.financeiro = Setor.objects.create(nome='Financeiro')
        self.de_fora = Funcionario.objects.create(
            nome='Caio Financeiro', matricula='3105', cargo='Analista', setor=self.financeiro, cpf='78855555555',
        )

    def _pedido(self, funcionario, dia=date(2026, 8, 3)):
        return AjustePonto.objects.create(
            funcionario=funcionario, data=dia, tipo_evento='entrada', horario_ajustado='08:00', motivo='Esqueci.',
        )

    def test_equipe_e_o_setor_dele_e_os_que_ele_responde_sem_gestores(self):
        usuario = self.gestor_user.usuario
        self.assertCountEqual(usuario.equipe(), [self.bia, self.davi])
        self.assertCountEqual(usuario.funcionarios_que_opera(), [self.bia, self.davi])
        self.assertCountEqual(usuario.setores_da_equipe(), [self.producao, self.envase])

    def test_dp_e_rh_operam_todos(self):
        Usuario.objects.filter(user=self.gestor_user).update(acesso_rh=True)
        usuario = Usuario.objects.get(user=self.gestor_user)
        self.assertIn(self.de_fora, usuario.funcionarios_que_opera())
        self.assertNotIn(self.de_fora, usuario.equipe())  # o "Ponto da Equipe" dele continua só com a equipe

    def test_espelho_so_da_equipe_com_valores_do_salario(self):
        url = reverse('ponto:listar_calculo')
        periodo = 'data_inicio=2026-08-03&data_fim=2026-08-07'

        resposta = self.client.get(f'{url}?funcionario={self.bia.id}&{periodo}')

        self.assertEqual(resposta.status_code, 200)
        self.assertIsNotNone(resposta.context['valores'])
        self.assertContains(resposta, 'Salário base R$ 3.000,00')
        self.assertContains(resposta, 'Equipe no dia')
        self.assertNotContains(resposta, reverse('ponto:listar_equipamentos'))
        self.assertCountEqual([f['id'] for f in resposta.context['funcionarios_busca']], [self.bia.id, self.davi.id])
        self.assertCountEqual(
            resposta.context['filtro_form'].fields['setor'].queryset, [self.producao, self.envase],
        )
        self.assertEqual(self.client.get(f'{url}?funcionario={self.de_fora.id}&{periodo}').status_code, 404)

    def test_corrige_batida_da_equipe_ja_aprovada(self):
        dia = date(2026, 8, 3)
        dados = {'data': dia.isoformat(), 'tipo_evento': 'saida', 'horario_ajustado': '17:00', 'motivo': 'Esqueceu.'}

        self.client.post(reverse('ponto:ajustar_marcacao', args=[self.bia.id, dia.isoformat()]), dados)
        resposta_de_fora = self.client.post(reverse('ponto:ajustar_marcacao', args=[self.de_fora.id, dia.isoformat()]), dados)

        ajuste = AjustePonto.objects.get(funcionario=self.bia)
        self.assertEqual(ajuste.status, 'aprovado')
        self.assertEqual(ajuste.analisado_por, self.gestor_user)
        self.assertEqual(resposta_de_fora.status_code, 404)
        self.assertFalse(AjustePonto.objects.filter(funcionario=self.de_fora).exists())

    def test_define_jornada_so_da_equipe(self):
        horario = _criar_horario_util(4302, saida2='16:00')
        dados = {'data_inicio': '2026-09-01', 'horario': horario.id}

        self.client.post(reverse('ponto:atribuir_jornada', args=[self.davi.id]), dados)
        resposta = self.client.post(reverse('ponto:atribuir_jornada', args=[self.de_fora.id]), dados)

        self.assertTrue(FuncionarioJornada.objects.filter(funcionario=self.davi, horario=horario).exists())
        self.assertEqual(resposta.status_code, 404)
        self.assertFalse(FuncionarioJornada.objects.filter(funcionario=self.de_fora).exists())

    def test_pdf_de_quem_e_de_fora_e_recusado(self):
        url = reverse('ponto:exportar_espelho')
        periodo = 'data_inicio=2026-08-03&data_fim=2026-08-07'

        for filtro in (f'filtro=setor&setor={self.financeiro.id}', f'filtro=funcionario&funcionario={self.de_fora.id}'):
            with self.subTest(filtro=filtro):
                resposta = self.client.get(f'{url}?{filtro}&{periodo}')
                self.assertEqual(resposta.status_code, 302)
                self.assertNotEqual(resposta.get('Content-Type'), 'application/pdf')

    def test_fila_de_ajustes_so_da_equipe(self):
        da_equipe = self._pedido(self.bia)
        de_fora = self._pedido(self.de_fora)

        resposta = self.client.get(reverse('ponto:ajustes_pendentes'))

        self.assertEqual(list(resposta.context['ajustes']), [da_equipe])
        self.assertEqual(resposta.context['badge_ajustes_pendentes'], 1)
        self.assertEqual(self.client.post(reverse('ponto:aprovar_ajuste', args=[de_fora.id])).status_code, 404)
        self.assertEqual(
            self.client.post(reverse('ponto:rejeitar_ajuste', args=[de_fora.id]), {'observacao_rh': 'Não.'}).status_code, 404,
        )
        de_fora.refresh_from_db()
        self.assertEqual(de_fora.status, 'pendente')

    def test_aprova_pedido_da_equipe(self):
        ajuste = self._pedido(self.davi)

        resposta = self.client.post(reverse('ponto:aprovar_ajuste', args=[ajuste.id]))

        self.assertRedirects(resposta, reverse('ponto:ajustes_pendentes'))
        ajuste.refresh_from_db()
        self.assertEqual(ajuste.status, 'aprovado')
        self.assertEqual(ajuste.analisado_por, self.gestor_user)

    def test_dia_com_batida_faltando_tem_corrigir_na_equipe_do_dia(self):
        segunda = date(2026, 8, 3)
        _bater_ponto(self.bia, self.equipamento, segunda, '08:00', '12:00', '13:00')

        resposta = self.client.get(reverse('ponto:ponto_equipe') + f'?data={segunda.isoformat()}')

        self.assertContains(resposta, reverse('ponto:ajustar_marcacao', args=[self.bia.id, segunda.isoformat()]) + '?tipo=saida')
        self.assertNotContains(resposta, reverse('ponto:ajustar_marcacao', args=[self.davi.id, segunda.isoformat()]) + '?tipo=saida')

    def test_atestado_lancado_pelo_gestor_abona_a_falta_e_mantem_a_assiduidade(self):
        url = reverse('ponto:listar_calculo') + f'?funcionario={self.bia.id}&data_inicio=2026-08-03&data_fim=2026-08-07'
        for dia in (3, 5, 6, 7):  # na terça, 04/08, a Bia não bateu
            _bater_ponto(self.bia, self.equipamento, date(2026, 8, dia), '08:00', '12:00', '13:00', '17:00')
        self.assertTrue(self.client.get(url).context['resultado']['totais']['perdeu_assiduidade'])

        resposta = self.client.post(
            reverse('lancar_atestado', args=[self.bia.id]),
            {'data_inicio': '2026-08-04', 'data_fim': '2026-08-04', 'motivo': 'Consulta', 'proxima': url},
        )

        self.assertRedirects(resposta, url, fetch_redirect_response=False)
        self.assertEqual(Atestado.objects.get(funcionario=self.bia).lancado_por, self.gestor_user)
        espelho = self.client.get(url)
        totais = espelho.context['resultado']['totais']
        self.assertFalse(totais['perdeu_assiduidade'])
        self.assertEqual(totais['dias_falta'], 0)
        self.assertContains(espelho, 'lançado por Gil Gestor')

    def test_gestor_nao_lanca_atestado_de_quem_e_de_fora(self):
        resposta = self.client.post(
            reverse('lancar_atestado', args=[self.de_fora.id]), {'data_inicio': '2026-08-04', 'data_fim': '2026-08-04'},
        )

        self.assertRedirects(resposta, reverse('dashboard'), fetch_redirect_response=False)
        self.assertFalse(Atestado.objects.filter(funcionario=self.de_fora).exists())

    def test_colaborador_continua_sem_acesso(self):
        user = User.objects.create_user(username='biaoperadora', password='senha123')
        Usuario.objects.create(user=user, funcionario=self.bia, papel='colaborador')
        self.client.login(username='biaoperadora', password='senha123')

        for nome in ('ponto:listar_calculo', 'ponto:ajustes_pendentes'):
            with self.subTest(nome=nome):
                self.assertRedirects(self.client.get(reverse(nome)), reverse('dashboard'), fetch_redirect_response=False)
