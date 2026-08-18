import threading
from datetime import date, datetime, timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from funcionarios.models import Funcionario, Setor, Usuario
from .calculos import calcular_periodo
from .controlid_client import ControlIdAPIError, ControlIdTimeoutError, ControlIdUsuarioNaoEncontradoError
from .models import (
    Equipamento, Escala, EscalaItem, Feriado, FuncionarioJornada, Horario, HorarioDia,
    MarcacaoRelogio, SincronizacaoFuncionario,
)
from .services import (
    _hash_dados_funcionario,
    coletar_marcacoes,
    processar_pendencias,
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

    def _linha_afd(self, nsr, cpf, momento_iso):
        # Formato oficial (leiaute Portaria 1510/671), tipo "3" = marcação de
        # ponto: NSR(9) + TIPO(1)='3' + DATA_ISO(24) + PREFIXO(1) + CPF(11) + CRC(4).
        return f'{nsr:09d}3{momento_iso}9{cpf}abcd'

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
    """Campo de horário/escala na tela de pré-cadastro (funcionarios/novo/)."""

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
            nome='Novo Funcionario', cpf=cpf, cargo='Analista', setor=self.setor.id,
            data_admissao='2026-08-17',
        )

    def test_cadastro_com_horario_cria_jornada_a_partir_da_admissao(self):
        horario = _criar_horario_util(5001)
        dados = self._dados_basicos('33333333333')
        dados['horario'] = horario.id

        self.client.post(reverse('cadastrar_funcionario'), dados)

        funcionario = Funcionario.objects.get(cpf='33333333333')
        jornada = FuncionarioJornada.objects.get(funcionario=funcionario)
        self.assertEqual(jornada.horario, horario)
        self.assertIsNone(jornada.escala)
        self.assertEqual(jornada.data_inicio, date(2026, 8, 17))

    def test_cadastro_com_escala_cria_jornada(self):
        escala = _criar_escala_12x36(5002, date(2026, 8, 1))
        dados = self._dados_basicos('44444444445')
        dados['escala'] = escala.id

        self.client.post(reverse('cadastrar_funcionario'), dados)

        funcionario = Funcionario.objects.get(cpf='44444444445')
        jornada = FuncionarioJornada.objects.get(funcionario=funcionario)
        self.assertEqual(jornada.escala, escala)
        self.assertIsNone(jornada.horario)

    def test_cadastro_sem_horario_nem_escala_nao_cria_jornada(self):
        dados = self._dados_basicos('44444444444')

        self.client.post(reverse('cadastrar_funcionario'), dados)

        funcionario = Funcionario.objects.get(cpf='44444444444')
        self.assertFalse(FuncionarioJornada.objects.filter(funcionario=funcionario).exists())

    def test_cadastro_com_horario_e_escala_juntos_e_rejeitado_e_nao_cria_funcionario(self):
        horario = _criar_horario_util(5003)
        escala = _criar_escala_12x36(5004, date(2026, 8, 1))
        dados = self._dados_basicos('55555555556')
        dados['horario'] = horario.id
        dados['escala'] = escala.id

        self.client.post(reverse('cadastrar_funcionario'), dados)

        self.assertFalse(Funcionario.objects.filter(cpf='55555555556').exists())


class SincronizarBotaoUnicoTests(TestCase):
    """
    Botão "Sincronizar" na própria tela de Equipamentos: um clique só faz
    envio de cadastro pendente + coleta de marcações, sem tela separada e
    sem detalhe por funcionário (pedido explícito depois de ver o Secullum).
    """

    def setUp(self):
        setor = Setor.objects.create(nome='RH')
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9999', cargo='RH', setor=setor, cpf='22222222222')
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.login(username='rhteste', password='senha123')

    @patch('ponto.services.client.get_afd', return_value='')
    @patch('ponto.services.client.buscar_usuario_por_cpf', return_value={'templates_count': 0})
    @patch('ponto.services.client.add_user')
    @patch('ponto.services.client.update_user', side_effect=ControlIdUsuarioNaoEncontradoError('Usuário não encontrado'))
    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_sincronizar_equipamento_resolve_so_daquele_equipamento(self, mock_login, mock_update, mock_add, mock_buscar, mock_afd):
        eq1 = _criar_equipamento(nome='Equip 1', ip='192.168.1.51')
        eq2 = _criar_equipamento(nome='Equip 2', ip='192.168.1.52')
        _criar_funcionario(cpf='33333333333')  # pendente nos dois equipamentos

        resposta = self.client.post(reverse('ponto:sincronizar_equipamento', args=[eq1.id]))

        self.assertRedirects(resposta, reverse('ponto:listar_equipamentos'))
        self.assertEqual(
            SincronizacaoFuncionario.objects.get(equipamento=eq1).status, 'aguardando_biometria'
        )
        self.assertEqual(
            SincronizacaoFuncionario.objects.get(equipamento=eq2).status, 'pendente'
        )

    @patch('ponto.services.client.get_afd', return_value='')
    @patch('ponto.services.client.buscar_usuario_por_cpf', return_value={'templates_count': 0})
    @patch('ponto.services.client.add_user')
    @patch('ponto.services.client.update_user', side_effect=ControlIdUsuarioNaoEncontradoError('Usuário não encontrado'))
    @patch('ponto.services.client.login', return_value='sessao-fake')
    def test_sincronizar_todos_resolve_todos_os_equipamentos(self, mock_login, mock_update, mock_add, mock_buscar, mock_afd):
        _criar_equipamento(nome='Equip 1', ip='192.168.1.51')
        _criar_equipamento(nome='Equip 2', ip='192.168.1.52')
        _criar_funcionario(cpf='33333333333')

        resposta = self.client.post(reverse('ponto:sincronizar_todos'))

        self.assertRedirects(resposta, reverse('ponto:listar_equipamentos'))
        self.assertEqual(SincronizacaoFuncionario.objects.filter(status='aguardando_biometria').count(), 2)

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

    def test_numero_duplicado_nao_permite_cadastro(self):
        Horario.objects.create(numero=2004, nome='Já existe')

        dados = {'numero': '2004', 'nome': 'Duplicado'}
        dados.update(_dados_formset_horario())

        self.client.post(reverse('ponto:cadastrar_horario'), dados)

        self.assertEqual(Horario.objects.filter(numero=2004).count(), 1)


class EscalaTests(TestCase):
    """
    Escala = rotação trabalho/folga (12x36 etc), separada de Horario --
    exatamente a regra que faltava: "um dia trabalhado sim, um dia não".
    """

    def setUp(self):
        setor = Setor.objects.create(nome='RH')
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9999', cargo='RH', setor=setor, cpf='22222222222')
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.login(username='rhteste', password='senha123')

    def test_12x36_alterna_trabalho_e_folga_por_data(self):
        horario_trabalho = Horario.objects.create(numero=3001, nome='12x36')
        escala = Escala.objects.create(
            numero=1, descricao='12x36 diurno', tamanho_ciclo=2, data_base=date(2026, 1, 1),
        )
        EscalaItem.objects.create(escala=escala, posicao=1, horario=horario_trabalho)
        EscalaItem.objects.create(escala=escala, posicao=2, horario=None)  # folga

        self.assertEqual(escala.horario_para_data(date(2026, 1, 1)), horario_trabalho)
        self.assertIsNone(escala.horario_para_data(date(2026, 1, 2)))
        self.assertEqual(escala.horario_para_data(date(2026, 1, 3)), horario_trabalho)
        self.assertIsNone(escala.horario_para_data(date(2026, 1, 4)))
        # depois de várias voltas do ciclo continua correto (offset par da data-base = dia de trabalho)
        self.assertEqual(escala.horario_para_data(date(2026, 1, 1) + timedelta(days=100)), horario_trabalho)
        self.assertIsNone(escala.horario_para_data(date(2026, 1, 1) + timedelta(days=101)))

    def test_cadastrar_escala_cria_um_item_por_posicao_do_ciclo(self):
        dados = {'descricao': 'Escala Teste', 'tamanho_ciclo': '3', 'data_base': '2026-01-01'}

        resposta = self.client.post(reverse('ponto:cadastrar_escala'), dados)

        escala = Escala.objects.get(descricao='Escala Teste')
        self.assertRedirects(resposta, reverse('ponto:editar_escala', args=[escala.id]))
        self.assertEqual(escala.itens.count(), 3)
        self.assertTrue(all(item.folga for item in escala.itens.all()))

    def test_tamanho_ciclo_nao_pode_ser_alterado_na_edicao(self):
        escala = Escala.objects.create(descricao='Fixa', tamanho_ciclo=2, data_base=date(2026, 1, 1))
        escala.itens.create(posicao=1)
        escala.itens.create(posicao=2)

        resposta = self.client.get(reverse('ponto:editar_escala', args=[escala.id]))

        self.assertTrue(resposta.context['form'].fields['tamanho_ciclo'].disabled)

    def test_excluir_escala(self):
        escala = Escala.objects.create(descricao='Para excluir', tamanho_ciclo=2, data_base=date(2026, 1, 1))

        resposta = self.client.post(reverse('ponto:excluir_escala', args=[escala.id]))

        self.assertRedirects(resposta, reverse('ponto:listar_escalas'))
        self.assertFalse(Escala.objects.filter(id=escala.id).exists())


class ImportarEscalasSecullumTests(TestCase):
    """Detecção de horário-vazio-como-folga usada pelo comando de importação."""

    def test_horario_vazio_e_reconhecido_como_folga(self):
        from ponto.management.commands.importar_escalas_secullum import _horario_esta_vazio

        dias_vazios = [{'entrada1': None, 'saida1': None, 'entrada2': None, 'saida2': None, 'entrada3': None, 'saida3': None}] * 7
        dias_com_horario = [dict(dias_vazios[0])]
        dias_com_horario[0]['entrada1'] = '07:00'

        self.assertTrue(_horario_esta_vazio(dias_vazios))
        self.assertFalse(_horario_esta_vazio(dias_com_horario))


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


def _criar_escala_12x36(numero, data_base, entrada1='06:00', saida1='18:00'):
    """
    Ciclo de 2 dias: posição 1 trabalha (turno de 12h diurno, mesmo padrão do
    horário 1001 real importado do Secullum), posição 2 folga. Turno virando
    a noite (entrada num dia, saída no outro) não é coberto ainda -- o motor
    de cálculo processa por dia calendário; ver `calculos.py`.
    """
    horario = Horario.objects.create(numero=numero, nome=f'12x36 {numero}')
    for dia in range(1, 8):
        HorarioDia.objects.create(horario=horario, dia_semana=dia, entrada1=entrada1, saida1=saida1)
    escala = Escala.objects.create(numero=numero, descricao=f'Escala {numero}', tamanho_ciclo=2, data_base=data_base)
    EscalaItem.objects.create(escala=escala, posicao=1, horario=horario)
    EscalaItem.objects.create(escala=escala, posicao=2, horario=None)
    return escala


def _bater_ponto(funcionario, equipamento, dia, *horarios):
    """Cria MarcacaoRelogio em sequência num dia, a partir de horas 'HH:MM'."""
    marcacoes = []
    for i, hhmm in enumerate(horarios):
        hora, minuto = map(int, hhmm.split(':'))
        momento = timezone.make_aware(datetime(dia.year, dia.month, dia.day, hora, minuto))
        marcacoes.append(MarcacaoRelogio.objects.create(
            equipamento=equipamento, funcionario=funcionario,
            id_evento_dispositivo=f'{dia.isoformat()}-{i}', momento=momento,
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
        data_base = date(2026, 8, 2)  # domingo, posição 1 do ciclo = dia de trabalho
        escala = _criar_escala_12x36(4005, data_base)
        FuncionarioJornada.objects.create(funcionario=self.funcionario, data_inicio=date(2026, 8, 1), escala=escala)
        _bater_ponto(self.funcionario, self.equipamento, data_base, '06:00', '19:00')  # 1h extra (13h trabalhadas)

        resultado = calcular_periodo(self.funcionario, data_base, data_base)

        self.assertEqual(resultado['totais']['extra_50_segundos'], 3600)
        self.assertEqual(resultado['totais']['extra_100_segundos'], 0)

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
