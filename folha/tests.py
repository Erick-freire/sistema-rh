import io
import os
import tempfile
from datetime import date, datetime
from decimal import Decimal

import openpyxl
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from funcionarios.models import Atestado, Funcionario, Setor, Usuario
from ponto.models import Equipamento, FuncionarioJornada, Horario, HorarioDia, MarcacaoRelogio

from . import colunas, premios, services
from .models import Fechamento, LinhaFechamento, RegraPremio

AGOSTO = date(2026, 8, 1)
JULHO = date(2026, 7, 1)


def _horario_util(numero=7001):
    """Segunda a sexta 08-12/13-17 (8h), sábado e domingo de folga."""
    horario = Horario.objects.create(numero=numero, nome=f'Jornada {numero}')
    for dia in range(1, 6):
        HorarioDia.objects.create(horario=horario, dia_semana=dia, entrada1='08:00', saida1='12:00', entrada2='13:00', saida2='17:00')
    for dia in (6, 7):
        HorarioDia.objects.create(horario=horario, dia_semana=dia)
    return horario


def _bater(funcionario, equipamento, dia, *horarios):
    for i, hhmm in enumerate(horarios):
        hora, minuto = map(int, hhmm.split(':'))
        MarcacaoRelogio.objects.create(
            equipamento=equipamento, funcionario=funcionario,
            id_evento_dispositivo=f'{funcionario.pk}-{dia.isoformat()}-{hhmm}-{i}',
            momento=timezone.make_aware(datetime(dia.year, dia.month, dia.day, hora, minuto)),
        )


class BaseFolhaTestCase(TestCase):
    def setUp(self):
        self.setor = Setor.objects.create(nome='Produção')
        self.equipamento = Equipamento.objects.create(nome='Relógio', ip='192.168.1.50', usuario_admin='admin', senha_admin='admin')
        self.ana = Funcionario.objects.create(
            nome='Ana Souza', matricula='1435', cargo='Auxiliar', setor=self.setor, cpf='11111111111', salario=Decimal('1000.00'),
        )
        FuncionarioJornada.objects.create(funcionario=self.ana, data_inicio=date(2026, 7, 1), horario=_horario_util())
        # 03/08/2026 (segunda): 1h a mais. Os outros 20 dias úteis de agosto ficam sem batida (Dia Falta).
        _bater(self.ana, self.equipamento, date(2026, 8, 3), '08:00', '12:00', '13:00', '18:00')

    def _abrir_calculado(self):
        """Abre agosto e calcula o ponto de todos (na tela, isso é feito em lotes)."""
        fechamento = services.abrir_fechamento(AGOSTO, None)
        while services.calcular_pendentes(fechamento):
            pass
        return fechamento


class ColunasTests(SimpleTestCase):
    def test_ler_horas_aceita_relogio_e_convencao_da_planilha(self):
        self.assertEqual(colunas.ler_horas('6:20'), 380)
        self.assertEqual(colunas.ler_horas('6,20'), 380)
        self.assertEqual(colunas.ler_horas('6.2'), 380)  # como o Excel guarda 6,20
        self.assertEqual(colunas.ler_horas('0,3'), 30)
        self.assertEqual(colunas.ler_horas('6:5'), 365)
        self.assertEqual(colunas.ler_horas('6'), 360)
        self.assertEqual(colunas.ler_horas(' '), 0)

    def test_ler_horas_recusa_minutos_acima_de_59_e_texto(self):
        for texto in ('6:75', '6,60', 'seis'):
            with self.subTest(texto=texto), self.assertRaises(colunas.ValorInvalido):
                colunas.ler_horas(texto)

    def test_ler_moeda(self):
        self.assertEqual(colunas.ler_moeda('1.234,56'), Decimal('1234.56'))
        self.assertEqual(colunas.ler_moeda('240,9'), Decimal('240.90'))
        self.assertEqual(colunas.ler_moeda('240.9'), Decimal('240.90'))
        self.assertEqual(colunas.ler_moeda('1.000'), Decimal('1000.00'))
        self.assertEqual(colunas.ler_moeda('R$ 100'), Decimal('100.00'))
        self.assertIsNone(colunas.ler_moeda(''))
        for texto in ('-5', 'abc'):
            with self.subTest(texto=texto), self.assertRaises(colunas.ValorInvalido):
                colunas.ler_moeda(texto)

    def test_vale_transporte_e_6_porcento_ou_valor(self):
        self.assertEqual(colunas.ler_vale_transporte('6%'), (True, None))
        self.assertEqual(colunas.ler_vale_transporte('6 %'), (True, None))
        self.assertEqual(colunas.ler_vale_transporte('162,75'), (False, Decimal('162.75')))
        self.assertEqual(colunas.ler_vale_transporte(''), (False, None))
        with self.assertRaises(colunas.ValorInvalido):
            colunas.ler_vale_transporte('5%')

    def test_exibicao(self):
        por_campo = colunas.COLUNAS_POR_CAMPO
        self.assertEqual(colunas.exibir(por_campo['he_50_minutos'], 380), '6:20')
        self.assertEqual(colunas.exibir(por_campo['he_50_minutos'], 0), '')
        self.assertEqual(colunas.exibir(por_campo['premio_programa'], Decimal('1234.5')), '1.234,50')
        self.assertEqual(colunas.exibir(por_campo['periculosidade'], Decimal('30.00')), '30%')
        self.assertEqual(colunas.exibir(por_campo['premio_safra'], Decimal('12.50')), '12,5%')
        self.assertEqual(colunas.exibir(por_campo['vale_transporte'], (True, None)), '6%')
        self.assertEqual(colunas.exibir(por_campo['assiduidade'], False), 'NÃO')
        self.assertEqual(colunas.exibir(por_campo['decimo_terceiro'], False), '')

    def test_xlsx_como_no_modelo(self):
        por_campo = colunas.COLUNAS_POR_CAMPO
        self.assertEqual(colunas.xlsx(por_campo['he_50_minutos'], 380), (6.2, '0.00'))
        self.assertEqual(colunas.xlsx(por_campo['he_50_minutos'], 0), (None, 'General'))
        self.assertEqual(colunas.xlsx(por_campo['vale_transporte'], (True, None)), (0.06, '0%'))
        self.assertEqual(colunas.xlsx(por_campo['vale_transporte'], (False, Decimal('21.00'))), (21.0, '#,##0.00'))
        self.assertEqual(colunas.xlsx(por_campo['periculosidade'], Decimal('30')), (0.3, '0%'))
        self.assertEqual(colunas.xlsx(por_campo['premio_safra'], Decimal('10')), (10.0, 'General'))
        self.assertEqual(colunas.xlsx(por_campo['assiduidade'], True), ('SIM', 'General'))
        self.assertEqual(colunas.xlsx(por_campo['matricula'], '1435'), (1435, 'General'))


class ServicesTests(BaseFolhaTestCase):
    def test_abrir_entra_so_quem_esta_ativo(self):
        Funcionario.objects.create(nome='Desligado', matricula='2', cargo='X', setor=self.setor, cpf='22222222222', status='inativo')
        Funcionario.objects.create(nome='Gestor placeholder', matricula='PENDENTE-1', cargo='Gestor (cadastro pendente)', setor=self.setor, cpf='00000000001')

        fechamento = services.abrir_fechamento(AGOSTO, None)

        self.assertEqual([linha.nome for linha in fechamento.linhas.all()], ['Ana Souza'])

    def test_abrir_deixa_o_ponto_pendente_para_calcular_em_lotes(self):
        Funcionario.objects.create(nome='Bruno Lima', matricula='1500', cargo='X', setor=self.setor, cpf='33333333333')
        fechamento = services.abrir_fechamento(AGOSTO, None)
        self.assertEqual(fechamento.linhas_pendentes().count(), 2)
        self.assertEqual(fechamento.linhas.get(matricula='1435').he_50_minutos, 0)

        self.assertEqual(services.calcular_pendentes(fechamento, limite=1), 1)
        self.assertEqual(services.calcular_pendentes(fechamento, limite=1), 0)

        self.assertEqual(fechamento.linhas.get(matricula='1435').he_50_minutos, 60)

    def test_colunas_do_ponto_vem_do_calculo_do_mes(self):
        linha = self._abrir_calculado().linhas.get()

        self.assertEqual(linha.he_50_minutos, 60)
        self.assertEqual(linha.faltas_dias, 20)
        self.assertFalse(linha.assiduidade)
        self.assertEqual(linha.codigo_empresa, 453)
        self.assertEqual(linha.matricula, '1435')

    def test_so_vt_e_periculosidade_vem_copiados_do_mes_anterior(self):
        julho = Fechamento.objects.create(competencia=JULHO, status='fechado')
        LinhaFechamento.objects.create(
            fechamento=julho, funcionario=self.ana, nome='Ana Souza', vt_seis_porcento=True, periculosidade=Decimal('30'),
            premio_programa=Decimal('300'), premio_bonus=Decimal('120'), descontos=Decimal('100'),
            reembolso_despesas=Decimal('50'), comissao=Decimal('900'), adiantamento_comissao=Decimal('900'),
            decimo_terceiro=True, he_50_minutos=999,
        )

        linha = self._abrir_calculado().linhas.get()

        self.assertTrue(linha.vt_seis_porcento)
        self.assertEqual(linha.periculosidade, Decimal('30'))
        # Lançamentos do mês começam em branco; prêmio programa vem das regras (não há nenhuma).
        for campo in ('premio_programa', 'premio_bonus', 'descontos', 'reembolso_despesas', 'comissao', 'adiantamento_comissao'):
            self.assertIsNone(getattr(linha, campo), campo)
        self.assertFalse(linha.decimo_terceiro)
        self.assertEqual(linha.he_50_minutos, 60)  # o ponto não é copiado: é recalculado

    def test_premios_vem_das_regras_e_o_recalculo_respeita_o_ajuste_do_rh(self):
        RegraPremio.objects.create(tipo='premio_programa', setor=self.setor, cargo='auxiliar', valor=Decimal('100'))
        RegraPremio.objects.create(tipo='premio_incentivo', funcionario=self.ana, valor=Decimal('218'))
        linha = self._abrir_calculado().linhas.get()
        self.assertEqual((linha.premio_programa, linha.premio_incentivo), (Decimal('100'), Decimal('218')))

        linha.premio_programa = Decimal('75')  # redução do mês, feita à mão
        linha.save()
        RegraPremio.objects.filter(tipo='premio_programa').update(valor=Decimal('120'))
        RegraPremio.objects.filter(tipo='premio_incentivo').update(valor=Decimal('250'))
        services.recalcular_linha(linha)

        linha.refresh_from_db()
        self.assertEqual(linha.premio_programa, Decimal('75'))
        self.assertTrue(linha.editado_pelo_rh('premio_programa'))
        self.assertEqual(linha.premio_incentivo, Decimal('250'))

    def test_recalcular_mantem_o_que_o_rh_corrigiu(self):
        linha = self._abrir_calculado().linhas.get()
        linha.he_50_minutos = 120
        linha.save()
        _bater(self.ana, self.equipamento, date(2026, 8, 4), '08:00', '12:00', '13:00', '17:00')

        services.recalcular_fechamento(linha.fechamento)

        linha.refresh_from_db()
        self.assertEqual(linha.he_50_minutos, 120)
        self.assertTrue(linha.editado_pelo_rh('he_50_minutos'))
        self.assertEqual(linha.faltas_dias, 19)  # o RH não mexeu: segue o cálculo

    def test_atestados_contam_os_dias_dentro_do_mes(self):
        Atestado.objects.create(funcionario=self.ana, data_inicio=date(2026, 7, 30), data_fim=date(2026, 8, 2))

        linha = self._abrir_calculado().linhas.get()

        self.assertEqual(linha.atestados_dias, 2)

    def test_quem_nao_usa_vale_transporte_nao_herda_o_desconto(self):
        julho = Fechamento.objects.create(competencia=JULHO, status='fechado')
        LinhaFechamento.objects.create(fechamento=julho, funcionario=self.ana, nome='Ana Souza', vt_seis_porcento=True)
        self.ana.usa_vale_transporte = False
        self.ana.save()

        linha = self._abrir_calculado().linhas.get()

        self.assertFalse(linha.vt_seis_porcento)
        self.assertIsNone(linha.vt_valor)

    def test_recalcular_tira_o_desconto_de_quem_passou_a_nao_usar(self):
        linha = self._abrir_calculado().linhas.get()
        linha.vt_valor = Decimal('21.00')
        linha.save()
        self.ana.usa_vale_transporte = False
        self.ana.save()
        self.assertTrue(any('não usa vale-transporte' in aviso for aviso in services.avisos(linha)))

        services.recalcular_linha(linha)

        linha.refresh_from_db()
        self.assertIsNone(linha.vt_valor)

    def test_aviso_quando_usa_vale_transporte_sem_desconto_informado(self):
        self.ana.usa_vale_transporte = True
        self.ana.save()
        linha = self._abrir_calculado().linhas.get()

        self.assertTrue(any('desconto não foi informado' in aviso for aviso in services.avisos(linha)))
        linha.vt_seis_porcento = True
        self.assertFalse(any('desconto não foi informado' in aviso for aviso in services.avisos(linha)))

    def test_aviso_quando_6_porcento_do_salario_e_menor_que_o_vt(self):
        linha = services.abrir_fechamento(AGOSTO, None).linhas.get()
        linha.vt_valor = Decimal('162.75')
        self.assertTrue(any('6% do salário (R$ 60,00)' in aviso for aviso in services.avisos(linha)))
        linha.vt_valor = Decimal('21.00')
        self.assertFalse(any('Vale-transporte' in aviso for aviso in services.avisos(linha)))


class ViewsTests(BaseFolhaTestCase):
    def setUp(self):
        super().setUp()
        # Inativo só para não entrar no fechamento: assim cada teste tem uma linha só (a da Ana).
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9999', cargo='RH', setor=self.setor, cpf='99999999999', status='inativo')
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.login(username='rhteste', password='senha123')

    def _abrir(self):
        return self._abrir_calculado()

    def _salvar(self, linha, campo, valor):
        return self.client.post(reverse('folha:salvar_celula', args=[linha.id]), {'campo': campo, 'valor': valor})

    def test_colaborador_nao_acessa(self):
        user = User.objects.create_user(username='colab', password='senha123')
        Usuario.objects.create(user=user, funcionario=self.ana, papel='colaborador')
        self.client.login(username='colab', password='senha123')

        resposta = self.client.get(reverse('folha:listar'))

        self.assertRedirects(resposta, reverse('dashboard'), fetch_redirect_response=False)

    def test_abrir_competencia_e_calcular_o_ponto_em_lotes(self):
        resposta = self.client.post(reverse('folha:abrir'), {'competencia': '2026-08-20'})  # qualquer dia do mês

        fechamento = Fechamento.objects.get(competencia=AGOSTO)
        self.assertRedirects(resposta, reverse('folha:detalhe', args=[fechamento.id]), fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse('folha:detalhe', args=[fechamento.id])), 'Calculando o ponto')

        lote = self.client.post(reverse('folha:calcular_lote', args=[fechamento.id])).json()

        self.assertEqual(lote, {'restantes': 0, 'total': 1})
        self.assertEqual(fechamento.linhas.get().he_50_minutos, 60)

    def test_com_ponto_pendente_nao_fecha_nem_exporta(self):
        fechamento = services.abrir_fechamento(AGOSTO, None)

        self.client.post(reverse('folha:fechar', args=[fechamento.id]))
        resposta = self.client.get(reverse('folha:exportar_xlsx', args=[fechamento.id]))

        fechamento.refresh_from_db()
        self.assertTrue(fechamento.aberto)
        self.assertRedirects(resposta, reverse('folha:detalhe', args=[fechamento.id]), fetch_redirect_response=False)

    def test_recalcular_marca_todos_para_o_calculo_em_lotes(self):
        fechamento = self._abrir()

        self.client.post(reverse('folha:recalcular', args=[fechamento.id]))

        self.assertEqual(fechamento.linhas_pendentes().count(), 1)

    def test_nao_abre_mes_futuro(self):
        proximo_ano = timezone.localdate().year + 1
        self.client.post(reverse('folha:abrir'), {'competencia': f'{proximo_ano}-01'})
        self.assertFalse(Fechamento.objects.exists())

    def test_detalhe_mostra_a_grade(self):
        fechamento = self._abrir()
        resposta = self.client.get(reverse('folha:detalhe', args=[fechamento.id]))
        self.assertContains(resposta, 'Ana Souza')
        self.assertContains(resposta, 'value="1:00"')  # H.E. 50%

    def test_salvar_celula_devolve_valor_formatado(self):
        linha = self._abrir().linhas.get()

        resposta = self._salvar(linha, 'premio_programa', '1.234,5')

        self.assertEqual(resposta.json()['texto'], '1.234,50')
        linha.refresh_from_db()
        self.assertEqual(linha.premio_programa, Decimal('1234.50'))

    def test_salvar_hora_do_ponto_marca_como_corrigida(self):
        linha = self._abrir().linhas.get()

        dados = self._salvar(linha, 'he_50_minutos', '2,00').json()

        self.assertEqual(dados['texto'], '2:00')
        self.assertTrue(dados['editado'])
        self.assertIn('calculado pelo ponto: 1:00', dados['titulo'])

    def test_ajustar_premio_da_regra_marca_como_ajustado(self):
        RegraPremio.objects.create(tipo='premio_programa', setor=self.setor, valor=Decimal('100'))
        linha = self._abrir().linhas.get()

        dados = self._salvar(linha, 'premio_programa', '75').json()

        self.assertTrue(dados['editado'])
        self.assertIn('pela regra de prêmios: 100,00', dados['titulo'])

    def test_salvar_vale_transporte_e_assiduidade(self):
        linha = self._abrir().linhas.get()
        self.assertEqual(self._salvar(linha, 'vale_transporte', '6%').json()['texto'], '6%')
        self.assertTrue(self._salvar(linha, 'assiduidade', '1').json()['marcado'])
        linha.refresh_from_db()
        self.assertTrue(linha.vt_seis_porcento)
        self.assertTrue(linha.assiduidade)

    def test_quem_nao_usa_vale_transporte_nao_recebe_desconto_na_grade(self):
        self.ana.usa_vale_transporte = False
        self.ana.save()
        fechamento = self._abrir()
        linha = fechamento.linhas.get()

        self.assertContains(self.client.get(reverse('folha:detalhe', args=[fechamento.id])), 'placeholder="não usa"')
        resposta = self._salvar(linha, 'vale_transporte', '6%')
        self.assertEqual(resposta.status_code, 400)
        self.assertIn('não usa vale-transporte', resposta.json()['erro'])
        self.assertEqual(self._salvar(linha, 'vale_transporte', '').status_code, 200)
        linha.refresh_from_db()
        self.assertFalse(linha.vt_seis_porcento)

    def test_salvar_valor_invalido_ou_coluna_do_cadastro(self):
        linha = self._abrir().linhas.get()
        resposta = self._salvar(linha, 'he_50_minutos', '6:75')
        self.assertEqual(resposta.status_code, 400)
        self.assertIn('minutos', resposta.json()['erro'])
        self.assertEqual(self._salvar(linha, 'nome', 'Outro nome').status_code, 400)

    def test_fechado_nao_aceita_edicao_e_pode_reabrir(self):
        fechamento = self._abrir()
        linha = fechamento.linhas.get()
        self.client.post(reverse('folha:fechar', args=[fechamento.id]))

        self.assertEqual(self._salvar(linha, 'premio_programa', '10').status_code, 409)
        self.client.post(reverse('folha:excluir', args=[fechamento.id]))
        self.assertTrue(Fechamento.objects.filter(id=fechamento.id).exists())  # fechado não se exclui

        self.client.post(reverse('folha:reabrir', args=[fechamento.id]))
        self.assertEqual(self._salvar(linha, 'premio_programa', '10').status_code, 200)

    def test_remover_e_incluir_funcionario(self):
        fechamento = self._abrir()
        linha = fechamento.linhas.get()

        self.client.post(reverse('folha:remover_linha', args=[linha.id]))
        self.assertFalse(fechamento.linhas.exists())

        self.client.post(reverse('folha:incluir_funcionario', args=[fechamento.id]), {'funcionario': self.ana.id})
        self.assertEqual(fechamento.linhas.get().he_50_minutos, 60)

    def test_exportar_xlsx_no_layout_do_modelo(self):
        fechamento = self._abrir()
        linha = fechamento.linhas.get()
        self._salvar(linha, 'vale_transporte', '6%')

        resposta = self.client.get(reverse('folha:exportar_xlsx', args=[fechamento.id]))

        self.assertIn('FOLHA AGOSTO 2026 - CONTABILIDADE - PREVIA.xlsx', resposta['Content-Disposition'])
        aba = openpyxl.load_workbook(io.BytesIO(resposta.content)).active
        self.assertEqual([celula.value for celula in aba[1]], [coluna.cabecalho for coluna in colunas.COLUNAS])
        self.assertEqual(aba['A2'].value, 453)
        self.assertEqual(aba['B2'].value, 1435)
        self.assertEqual(aba['G2'].value, 1.0)  # H.E. 50%: 1h = 1.00
        self.assertEqual(aba['G2'].number_format, '0.00')
        self.assertEqual(aba['R2'].value, 20)  # faltas (dias)
        self.assertEqual(aba['S2'].value, 'NÃO')
        self.assertEqual((aba['T2'].value, aba['T2'].number_format), (0.06, '0%'))

    def test_exportar_pdf(self):
        fechamento = self._abrir()

        resposta = self.client.get(reverse('folha:exportar_pdf', args=[fechamento.id]))

        self.assertEqual(resposta['Content-Type'], 'application/pdf')
        self.assertTrue(resposta.content.startswith(b'%PDF'))


class RegrasPremioTests(TestCase):
    def setUp(self):
        self.producao = Setor.objects.create(nome='Produção')
        self.moega = Setor.objects.create(nome='Moega')
        self.ana = Funcionario.objects.create(nome='Ana', matricula='1', cargo='Auxiliar de Produção', setor=self.producao, cpf='11111111111')
        self.bia = Funcionario.objects.create(nome='Bia', matricula='2', cargo='Operador de Máquina 2', setor=self.producao, cpf='22222222222')
        self.caio = Funcionario.objects.create(nome='Caio', matricula='3', cargo='Auxiliar de Produção', setor=self.moega, cpf='33333333333')

    def _regra(self, **campos):
        regra = RegraPremio(tipo=campos.pop('tipo', 'premio_programa'), **campos)
        regra.full_clean()
        regra.save()
        return regra

    def test_a_regra_mais_especifica_vence(self):
        self._regra(setor=self.producao, valor=Decimal('100'))
        self._regra(setor=self.producao, cargo='OPERADOR DE MAQUINA 2', valor=Decimal('450'))  # sem acento e maiúsculo
        self._regra(funcionario=self.ana, valor=Decimal('0'))  # exceção: não recebe

        regras = premios.RegrasDoMes(AGOSTO)

        self.assertIsNone(regras.valores_para(self.ana)['premio_programa'])
        self.assertEqual(regras.valores_para(self.bia)['premio_programa'], Decimal('450'))
        self.assertIsNone(regras.valores_para(self.caio)['premio_programa'])  # outro setor

    def test_vigencia(self):
        self._regra(tipo='premio_safra', setor=self.moega, valor=Decimal('10'), vigente_de=date(2026, 6, 1), vigente_ate=date(2026, 9, 1))

        self.assertEqual(premios.RegrasDoMes(date(2026, 9, 1)).valores_para(self.caio)['premio_safra'], Decimal('10'))
        self.assertIsNone(premios.RegrasDoMes(date(2026, 10, 1)).valores_para(self.caio)['premio_safra'])
        self.assertIsNone(premios.RegrasDoMes(date(2026, 5, 1)).valores_para(self.caio)['premio_safra'])

    def test_nao_aceita_duas_regras_para_o_mesmo_alvo_no_mesmo_periodo(self):
        self._regra(setor=self.producao, cargo='Auxiliar de Produção', valor=Decimal('100'), vigente_ate=date(2026, 9, 1))
        with self.assertRaises(ValidationError):
            self._regra(setor=self.producao, cargo='auxiliar de producao', valor=Decimal('120'))
        # Depois que a primeira termina, pode.
        self._regra(setor=self.producao, cargo='Auxiliar de Produção', valor=Decimal('120'), vigente_de=date(2026, 10, 1))
        self.assertEqual(premios.RegrasDoMes(date(2026, 10, 1)).valores_para(self.ana)['premio_programa'], Decimal('120'))

    def test_pessoa_ou_setor_nunca_os_dois(self):
        with self.assertRaises(ValidationError):
            self._regra(setor=self.producao, funcionario=self.ana, valor=Decimal('100'))
        with self.assertRaises(ValidationError):
            self._regra(valor=Decimal('100'))

    def test_sugestoes_e_diferencas_a_partir_do_fechamento(self):
        setembro = Fechamento.objects.create(competencia=date(2026, 9, 1), status='fechado')
        auxiliar_moega = Funcionario.objects.create(nome='Davi', matricula='4', cargo='auxiliar de produção', setor=self.moega, cpf='44444444444')
        for funcionario, valor in ((self.caio, '100'), (auxiliar_moega, '75'), (self.ana, '300'), (self.bia, None)):
            LinhaFechamento.objects.create(
                fechamento=setembro, funcionario=funcionario, nome=funcionario.nome,
                premio_programa=Decimal(valor) if valor else None,
            )

        sugestoes = premios.sugestoes_por_setor_e_cargo(setembro, 'premio_programa')

        # Moega/auxiliar empata 100 x 75 (sem maioria): não vira sugestão.
        self.assertEqual([(s['setor'], s['valor']) for s in sugestoes], [(self.producao, Decimal('300'))])
        self.assertEqual(
            {item['linha'].funcionario for item in premios.diferencas(setembro, 'premio_programa')},
            {self.caio, auxiliar_moega, self.ana},
        )


class PremiosViewsTests(BaseFolhaTestCase):
    def setUp(self):
        super().setUp()
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9999', cargo='RH', setor=self.setor, cpf='99999999999', status='inativo')
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.login(username='rhteste', password='senha123')

    def test_criar_regra_de_setor_e_cargo(self):
        resposta = self.client.post(reverse('folha:premios'), {
            'tipo': 'premio_programa', 'alvo': 'setor_cargo', 'setor': self.setor.id, 'cargo': ' auxiliar ',
            'funcionario': self.ana.id,  # ignorado: o alvo é setor e cargo
            'valor': '300', 'vigente_de': '', 'vigente_ate': '',
        })

        regra = RegraPremio.objects.get()
        self.assertRedirects(resposta, reverse('folha:premios') + '#premio_programa', fetch_redirect_response=False)
        self.assertEqual((regra.setor, regra.cargo, regra.funcionario, regra.valor), (self.setor, 'auxiliar', None, Decimal('300')))
        pagina = self.client.get(reverse('folha:premios'))
        self.assertContains(pagina, 'Produção · auxiliar')
        self.assertContains(pagina, 'Quem recebe em')

    def test_regra_de_safra_com_vigencia(self):
        self.client.post(reverse('folha:premios'), {
            'tipo': 'premio_safra', 'alvo': 'setor', 'setor': self.setor.id, 'cargo': 'qualquer',
            'valor': '10', 'vigente_de': '2026-06-15', 'vigente_ate': '2026-09-30',  # dias quaisquer do calendário
        })

        regra = RegraPremio.objects.get()
        self.assertEqual((regra.cargo, regra.vigente_de, regra.vigente_ate), ('', date(2026, 6, 1), date(2026, 9, 1)))

    def test_regra_de_pessoa_sem_pessoa_nao_salva(self):
        resposta = self.client.post(reverse('folha:premios'), {'tipo': 'premio_incentivo', 'alvo': 'pessoa', 'valor': '218'})

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Escolha a pessoa.')
        self.assertFalse(RegraPremio.objects.exists())

    def test_editar_e_excluir(self):
        regra = RegraPremio.objects.create(tipo='premio_programa', setor=self.setor, valor=Decimal('100'))

        self.client.post(reverse('folha:editar_regra', args=[regra.id]), {
            'tipo': 'premio_programa', 'alvo': 'setor', 'setor': self.setor.id, 'valor': '150',
        })
        regra.refresh_from_db()
        self.assertEqual(regra.valor, Decimal('150'))

        self.client.post(reverse('folha:excluir_regra', args=[regra.id]))
        self.assertFalse(RegraPremio.objects.exists())

    def test_criar_sugeridas_e_fixar_a_partir_do_ultimo_fechamento(self):
        bia = Funcionario.objects.create(nome='Bia', matricula='2', cargo='Auxiliar', setor=self.setor, cpf='22222222222')
        caio = Funcionario.objects.create(nome='Caio', matricula='3', cargo='Auxiliar', setor=self.setor, cpf='33333333333')
        setembro = Fechamento.objects.create(competencia=date(2026, 9, 1), status='fechado')
        for funcionario, valor in ((self.ana, '300'), (bia, '300'), (caio, '450')):
            LinhaFechamento.objects.create(fechamento=setembro, funcionario=funcionario, nome=funcionario.nome, premio_programa=Decimal(valor))

        self.client.post(reverse('folha:sugerir_regras'), {'tipo': 'premio_programa'})
        regra_grupo = RegraPremio.objects.get()
        self.assertEqual((regra_grupo.cargo, regra_grupo.valor), ('Auxiliar', Decimal('300')))
        self.assertContains(self.client.get(reverse('folha:premios')), 'Fixar 450,00 para a pessoa')

        self.client.post(reverse('folha:fixar_regra'), {'tipo': 'premio_programa', 'funcionario': caio.id, 'valor': '450.00'})
        regra_caio = RegraPremio.objects.get(funcionario=caio)
        self.assertEqual(regra_caio.valor, Decimal('450'))
        self.assertEqual(premios.diferencas(setembro, 'premio_programa'), [])

    def test_safra_nao_tem_criacao_em_lote(self):
        setembro = Fechamento.objects.create(competencia=date(2026, 9, 1), status='fechado')
        LinhaFechamento.objects.create(fechamento=setembro, funcionario=self.ana, nome='Ana', premio_safra=Decimal('10'))

        self.client.post(reverse('folha:fixar_regra'), {'tipo': 'premio_safra', 'funcionario': self.ana.id, 'valor': '10'})
        self.client.post(reverse('folha:sugerir_regras'), {'tipo': 'premio_safra'})

        self.assertFalse(RegraPremio.objects.exists())


class ImportarPlanilhaTests(TestCase):
    def setUp(self):
        setor = Setor.objects.create(nome='Produção')
        self.ana = Funcionario.objects.create(nome='Ana Souza', matricula='1435', cargo='X', setor=setor, cpf='11111111111')
        self.pasta = tempfile.TemporaryDirectory()
        self.addCleanup(self.pasta.cleanup)

    def _planilha(self, linhas, cabecalho=None):
        planilha = openpyxl.Workbook()
        aba = planilha.active
        aba.append(cabecalho or [coluna.cabecalho for coluna in colunas.COLUNAS])
        for linha in linhas:
            aba.append(linha)
        caminho = os.path.join(self.pasta.name, 'folha.xlsx')
        planilha.save(caminho)
        return caminho

    def _linha(self, **valores):
        padrao = {coluna.campo: None for coluna in colunas.COLUNAS}
        padrao.update(valores)
        return [padrao[coluna.campo] for coluna in colunas.COLUNAS]

    def test_importa_como_fechamento_fechado(self):
        caminho = self._planilha([
            self._linha(
                codigo_empresa=461, matricula=1435, nome='ANA SOUZA', he_50_minutos=17.41, he_100_minutos=5.39,
                premio_programa=100, vale_transporte=0.06, periculosidade=0.3, premio_safra=10, assiduidade='SIM',
            ),
            self._linha(codigo_empresa=453, matricula=1479, nome='NOVATO', vale_transporte=162.75),
        ])

        saida = io.StringIO()
        call_command('importar_fechamento_planilha', caminho, competencia='2026-09', stdout=saida)

        fechamento = Fechamento.objects.get(competencia=date(2026, 9, 1))
        self.assertEqual(fechamento.status, 'fechado')
        linha = fechamento.linhas.get()
        self.assertEqual((linha.he_50_minutos, linha.he_100_minutos), (17 * 60 + 41, 5 * 60 + 39))
        self.assertEqual(linha.premio_programa, Decimal('100.00'))
        self.assertTrue(linha.vt_seis_porcento)
        self.assertEqual(linha.periculosidade, Decimal('30.00'))
        self.assertEqual(linha.premio_safra, Decimal('10.00'))
        self.assertTrue(linha.assiduidade)
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.codigo_empresa, 461)
        self.assertIn('1479 NOVATO', saida.getvalue())

    def test_recalcular_linha_importada_nao_passa_por_cima_das_horas_nem_dos_premios(self):
        caminho = self._planilha([
            self._linha(codigo_empresa=453, matricula=1435, nome='ANA', he_50_minutos=6.2, premio_programa=75),
        ])
        call_command('importar_fechamento_planilha', caminho, competencia='2026-09', stdout=io.StringIO())
        RegraPremio.objects.create(tipo='premio_programa', funcionario=self.ana, valor=Decimal('100'))
        linha = LinhaFechamento.objects.get()

        services.recalcular_linha(linha)

        linha.refresh_from_db()
        self.assertEqual(linha.he_50_minutos, 380)
        self.assertEqual(linha.premio_programa, Decimal('75'))

    def test_recusa_planilha_em_outro_layout(self):
        caminho = self._planilha([], cabecalho=['NOME', 'CPF'])
        with self.assertRaises(CommandError):
            call_command('importar_fechamento_planilha', caminho, competencia='2026-09', stdout=io.StringIO())

    def test_dry_run_nao_grava(self):
        caminho = self._planilha([self._linha(codigo_empresa=461, matricula=1435, nome='ANA')])
        call_command('importar_fechamento_planilha', caminho, competencia='2026-09', dry_run=True, stdout=io.StringIO())
        self.assertFalse(Fechamento.objects.exists())
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.codigo_empresa, 453)
