import importlib
import io
import os
import tempfile
from datetime import date, datetime
from decimal import Decimal

import openpyxl
from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from funcionarios.models import Atestado, Funcionario, Setor, Usuario
from ponto.models import Equipamento, FuncionarioJornada, Horario, HorarioDia, MarcacaoRelogio

from . import colunas, premios, services
from .models import (
    Fechamento, LinhaFechamento, MembroPremioPrograma, MembroPremioSafra, RegraPremio, RegraPremioPrograma,
    RegraPremioSafra,
)

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


def _regra_programa(valor, *funcionarios, cargo='Auxiliar'):
    regra = RegraPremioPrograma.objects.create(cargo=cargo, valor=Decimal(valor))
    for funcionario in funcionarios:
        MembroPremioPrograma.objects.create(regra=regra, funcionario=funcionario)
    return regra


def _regra_safra(*funcionarios, nome='Safra 2026', percentual='10', de=None, ate=None):
    regra = RegraPremioSafra.objects.create(nome=nome, percentual=Decimal(percentual), vigente_de=de, vigente_ate=ate)
    for funcionario in funcionarios:
        MembroPremioSafra.objects.create(regra=regra, funcionario=funcionario)
    return regra


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

    def test_adicional_noturno_vai_em_horas_convertidas(self):
        # 04/08: jornada inteira e mais 1h45 de relógio depois das 22:00 = 2h na hora de 52min30s.
        _bater(self.ana, self.equipamento, date(2026, 8, 4), '08:00', '12:00', '13:00', '17:00', '22:00', '23:45')

        linha = self._abrir_calculado().linhas.get()

        self.assertEqual(linha.adicional_noturno_minutos, 120)

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

    def test_reducao_do_mes_anterior_nao_passa_para_o_mes_seguinte(self):
        _regra_programa('300', self.ana)
        julho = Fechamento.objects.create(competencia=JULHO, status='fechado')
        LinhaFechamento.objects.create(fechamento=julho, funcionario=self.ana, nome='Ana Souza', premio_programa=Decimal('150'))

        linha = self._abrir_calculado().linhas.get()

        self.assertEqual(linha.premio_programa, Decimal('300'))  # todo mês começa com o valor cheio da regra

    def test_premios_vem_das_regras_e_o_recalculo_respeita_o_ajuste_do_rh(self):
        _regra_programa('100', self.ana)
        RegraPremio.objects.create(tipo='premio_incentivo', funcionario=self.ana, valor=Decimal('218'))
        linha = self._abrir_calculado().linhas.get()
        self.assertEqual((linha.premio_programa, linha.premio_incentivo), (Decimal('100'), Decimal('218')))

        linha.premio_programa = Decimal('75')  # redução do mês, feita à mão
        linha.save()
        RegraPremioPrograma.objects.update(valor=Decimal('120'))
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
        _regra_programa('100', self.ana)
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
        regra = RegraPremio(tipo=campos.pop('tipo', 'premio_incentivo'), **campos)
        regra.full_clean()
        regra.save()
        return regra

    def test_a_regra_mais_especifica_vence(self):
        self._regra(setor=self.producao, valor=Decimal('100'))
        self._regra(setor=self.producao, cargo='OPERADOR DE MAQUINA 2', valor=Decimal('450'))  # sem acento e maiúsculo
        self._regra(funcionario=self.ana, valor=Decimal('0'))  # exceção: não recebe

        regras = premios.RegrasDoMes(AGOSTO)

        self.assertIsNone(regras.valores_para(self.ana)['premio_incentivo'])
        self.assertEqual(regras.valores_para(self.bia)['premio_incentivo'], Decimal('450'))
        self.assertIsNone(regras.valores_para(self.caio)['premio_incentivo'])  # outro setor

    def test_vigencia(self):
        self._regra(setor=self.moega, valor=Decimal('100'), vigente_de=date(2026, 6, 1), vigente_ate=date(2026, 9, 1))

        self.assertEqual(premios.RegrasDoMes(date(2026, 9, 1)).valores_para(self.caio)['premio_incentivo'], Decimal('100'))
        self.assertIsNone(premios.RegrasDoMes(date(2026, 10, 1)).valores_para(self.caio)['premio_incentivo'])
        self.assertIsNone(premios.RegrasDoMes(date(2026, 5, 1)).valores_para(self.caio)['premio_incentivo'])

    def test_nao_aceita_duas_regras_para_o_mesmo_alvo_no_mesmo_periodo(self):
        self._regra(setor=self.producao, cargo='Auxiliar de Produção', valor=Decimal('100'), vigente_ate=date(2026, 9, 1))
        with self.assertRaises(ValidationError):
            self._regra(setor=self.producao, cargo='auxiliar de producao', valor=Decimal('120'))
        # Depois que a primeira termina, pode.
        self._regra(setor=self.producao, cargo='Auxiliar de Produção', valor=Decimal('120'), vigente_de=date(2026, 10, 1))
        self.assertEqual(premios.RegrasDoMes(date(2026, 10, 1)).valores_para(self.ana)['premio_incentivo'], Decimal('120'))

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
                premio_incentivo=Decimal(valor) if valor else None,
            )

        sugestoes = premios.sugestoes_por_setor_e_cargo(setembro, 'premio_incentivo')

        # Moega/auxiliar empata 100 x 75 (sem maioria): não vira sugestão.
        self.assertEqual([(s['setor'], s['valor']) for s in sugestoes], [(self.producao, Decimal('300'))])
        self.assertEqual(
            {item['linha'].funcionario for item in premios.diferencas(setembro, 'premio_incentivo')},
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
            'tipo': 'premio_incentivo', 'alvo': 'setor_cargo', 'setor': self.setor.id, 'cargo': ' auxiliar ',
            'funcionario': self.ana.id,  # ignorado: o alvo é setor e cargo
            'valor': '300', 'vigente_de': '', 'vigente_ate': '',
        })

        regra = RegraPremio.objects.get()
        self.assertRedirects(resposta, reverse('folha:premios') + '#premio_incentivo', fetch_redirect_response=False)
        self.assertEqual((regra.setor, regra.cargo, regra.funcionario, regra.valor), (self.setor, 'auxiliar', None, Decimal('300')))
        pagina = self.client.get(reverse('folha:premios'))
        self.assertContains(pagina, 'Produção · auxiliar')
        self.assertContains(pagina, 'Quem recebe em')

    def test_regra_com_vigencia(self):
        self.client.post(reverse('folha:premios'), {
            'alvo': 'setor', 'setor': self.setor.id, 'cargo': 'qualquer',
            'valor': '100', 'vigente_de': '2026-06-15', 'vigente_ate': '2026-09-30',  # dias quaisquer do calendário
        })

        regra = RegraPremio.objects.get()
        self.assertEqual(regra.tipo, 'premio_incentivo')
        self.assertEqual((regra.cargo, regra.vigente_de, regra.vigente_ate), ('', date(2026, 6, 1), date(2026, 9, 1)))

    def test_regra_de_pessoa_sem_pessoa_nao_salva(self):
        resposta = self.client.post(reverse('folha:premios'), {'tipo': 'premio_incentivo', 'alvo': 'pessoa', 'valor': '218'})

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Escolha a pessoa.')
        self.assertFalse(RegraPremio.objects.exists())

    def test_editar_e_excluir(self):
        regra = RegraPremio.objects.create(tipo='premio_incentivo', setor=self.setor, valor=Decimal('100'))

        self.client.post(reverse('folha:editar_regra', args=[regra.id]), {
            'tipo': 'premio_incentivo', 'alvo': 'setor', 'setor': self.setor.id, 'valor': '150',
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
            LinhaFechamento.objects.create(fechamento=setembro, funcionario=funcionario, nome=funcionario.nome, premio_incentivo=Decimal(valor))

        self.client.post(reverse('folha:sugerir_regras'), {'tipo': 'premio_incentivo'})
        regra_grupo = RegraPremio.objects.get()
        self.assertEqual((regra_grupo.cargo, regra_grupo.valor), ('Auxiliar', Decimal('300')))
        self.assertContains(self.client.get(reverse('folha:premios')), 'Fixar 450,00 para a pessoa')

        self.client.post(reverse('folha:fixar_regra'), {'tipo': 'premio_incentivo', 'funcionario': caio.id, 'valor': '450.00'})
        regra_caio = RegraPremio.objects.get(funcionario=caio)
        self.assertEqual(regra_caio.valor, Decimal('450'))
        self.assertEqual(premios.diferencas(setembro, 'premio_incentivo'), [])

    def test_sugerir_e_fixar_so_valem_para_o_incentivo(self):
        setembro = Fechamento.objects.create(competencia=date(2026, 9, 1), status='fechado')
        LinhaFechamento.objects.create(fechamento=setembro, funcionario=self.ana, nome='Ana', premio_safra=Decimal('10'))

        self.client.post(reverse('folha:fixar_regra'), {'tipo': 'premio_safra', 'funcionario': self.ana.id, 'valor': '10'})
        self.client.post(reverse('folha:sugerir_regras'), {'tipo': 'premio_safra'})

        self.assertFalse(RegraPremio.objects.exists())
        self.assertFalse(RegraPremioSafra.objects.exists())


class PremioProgramaTests(TestCase):
    """Programa: regra por cargo com a lista de quem recebe (pedido do usuário em 29/10/2026)."""

    def setUp(self):
        producao = Setor.objects.create(nome='Produção')
        self.ana = Funcionario.objects.create(nome='Ana', matricula='1', cargo='Auxiliar', setor=producao, cpf='11111111111')
        self.bia = Funcionario.objects.create(nome='Bia', matricula='2', cargo='Auxiliar', setor=producao, cpf='22222222222')

    def test_so_recebe_quem_esta_na_lista_da_regra(self):
        _regra_programa('100', self.bia)

        regras = premios.RegrasDoMes(AGOSTO)

        self.assertEqual(regras.valores_para(self.bia)['premio_programa'], Decimal('100'))
        self.assertIsNone(regras.valores_para(self.ana)['premio_programa'])  # mesmo cargo, mas fora da lista

    def test_cada_pessoa_fica_em_uma_regra_so(self):
        _regra_programa('100', self.ana)
        outra = _regra_programa('300')
        with self.assertRaises(IntegrityError), transaction.atomic():
            MembroPremioPrograma.objects.create(regra=outra, funcionario=self.ana)

    def test_nao_aceita_duas_regras_com_o_mesmo_cargo_e_valor(self):
        _regra_programa('450', cargo='Operador de Máquina 2')
        with self.assertRaises(ValidationError):
            RegraPremioPrograma(cargo=' OPERADOR DE  MAQUINA 2', valor=Decimal('450')).full_clean()
        RegraPremioPrograma(cargo='Operador de Máquina 2', valor=Decimal('900')).full_clean()  # outro valor pode


class RegrasProgramaDoFechamentoTests(TestCase):
    """Montagem das regras de programa a partir de um fechamento (a planilha de set/2026)."""

    def setUp(self):
        self.setembro = Fechamento.objects.create(competencia=date(2026, 9, 1), status='fechado')
        self.setores = {}

    def _linha(self, nome, cargo, setor, valor, **ausencias):
        setor = self.setores.setdefault(setor, Setor.objects.create(nome=setor))
        funcionario = Funcionario.objects.create(
            nome=nome, matricula=str(Funcionario.objects.count() + 1), cargo=cargo, setor=setor,
            cpf=f'{Funcionario.objects.count() + 1:011d}',
        )
        LinhaFechamento.objects.create(
            fechamento=self.setembro, funcionario=funcionario, nome=nome,
            premio_programa=Decimal(valor) if valor else None, **ausencias,
        )
        return funcionario

    def _plano(self):
        return {
            item['linha'].nome: (item['valor'], item['reduzido'], bool(item['confira']))
            for item in premios.planejar_regras_programa(self.setembro)
        }

    def test_quem_teve_reducao_entra_na_regra_do_valor_cheio(self):
        for nome, valor in (('A1', '100'), ('A2', '100'), ('A3', '300'), ('A4', '300')):
            self._linha(nome, 'Auxiliar de Produção', 'Macarrão', valor)
        self._linha('A5', 'auxiliar de producao', 'Macarrão', '90', atestados_dias=1)
        self._linha('A6', 'Auxiliar de Produção', 'Macarrão', '150', atestados_dias=1)
        self._linha('A7', 'Auxiliar de Produção', 'Macarrão', None)  # não recebe: fica fora
        for nome in ('O1', 'O2', 'O3'):
            self._linha(nome, 'Operador de Máquina 2', 'Macarrão', '450')
        self._linha('O4', 'Operador de Máquina 2', 'Macarrão', '157.50')  # sem falta registrada, mas reduzido
        self._linha('P1', 'Operador de Máquina 1', 'Macarrão', '300')
        self._linha('P2', 'Operador de Máquina 1', 'Macarrão', '450')  # um contra um: os dois valem
        self._linha('M1', 'Auxiliar de Produção', 'Moinho', '100')
        self._linha('M2', 'Auxiliar de Produção', 'Moinho', '100', faltas_minutos=600)
        self._linha('M3', 'Auxiliar de Produção', 'Moinho', '200')
        self._linha('M4', 'Auxiliar de Produção', 'Moinho', '200')
        self._linha('S1', 'Serviços Gerais 1', 'Limpeza', '200', assiduidade=False)
        self._linha('S2', 'Serviços Gerais 1', 'Carga', '300')

        plano = self._plano()

        self.assertNotIn('A7', plano)
        self.assertEqual(plano['A1'], (Decimal('100'), False, False))
        self.assertEqual(plano['A5'], (Decimal('100'), True, False))
        self.assertEqual(plano['A6'], (Decimal('300'), True, True))  # entre 100 e 300: confira
        self.assertEqual(plano['O4'], (Decimal('450'), True, False))
        self.assertEqual((plano['P1'][0], plano['P2'][0]), (Decimal('300'), Decimal('450')))
        self.assertEqual(plano['M1'], (Decimal('100'), False, False))  # confirmado pelo M2
        self.assertEqual(plano['S1'], (Decimal('300'), True, True))  # referência de outro setor

    def test_criar_regras_agrupa_por_cargo_e_valor_e_pode_rodar_de_novo(self):
        ana = self._linha('Ana', 'Auxiliar de Produção', 'Macarrão', '100')
        bia = self._linha('Bia', 'AUXILIAR DE PRODUCAO', 'Macarrão', '100')
        caio = self._linha('Caio', 'Auxiliar de Produção', 'Moinho', '200')
        RegraPremio.objects.create(tipo='premio_programa', setor=ana.setor, valor=Decimal('50'))  # do modelo antigo

        call_command('criar_regras_premio_programa', competencia='2026-09', dry_run=True, stdout=io.StringIO())
        self.assertFalse(RegraPremioPrograma.objects.exists())
        self.assertTrue(RegraPremio.objects.exists())

        saida = io.StringIO()
        call_command('criar_regras_premio_programa', competencia='2026-09', stdout=saida)

        regras = {(regra.cargo, regra.valor): regra for regra in RegraPremioPrograma.objects.all()}
        self.assertEqual(set(regras), {('Auxiliar de Produção', Decimal('100')), ('Auxiliar de Produção', Decimal('200'))})
        self.assertEqual(
            {membro.funcionario for membro in regras[('Auxiliar de Produção', Decimal('100'))].membros.all()}, {ana, bia},
        )
        self.assertEqual(caio.membro_premio_programa.regra.valor, Decimal('200'))
        self.assertFalse(RegraPremio.objects.exists())
        self.assertIn('2 regra(s) criada(s), 3 pessoa(s) incluída(s)', saida.getvalue())

        criadas, incluidas, puladas = premios.criar_regras_programa(premios.planejar_regras_programa(self.setembro))
        self.assertEqual((criadas, incluidas, len(puladas)), ([], 0, 3))


class PremioProgramaViewsTests(BaseFolhaTestCase):
    def setUp(self):
        super().setUp()
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9999', cargo='RH', setor=self.setor, cpf='99999999999', status='inativo')
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.login(username='rhteste', password='senha123')
        self.bia = Funcionario.objects.create(nome='Bia', matricula='2', cargo='Operador', setor=self.setor, cpf='22222222222')

    def test_criar_regra_colocar_pessoas_e_o_fechamento_usar(self):
        resposta = self.client.post(reverse('folha:criar_regra_programa'), {'cargo': ' Auxiliar ', 'valor': '100'})

        regra = RegraPremioPrograma.objects.get()
        self.assertRedirects(resposta, reverse('folha:regra_programa', args=[regra.id]), fetch_redirect_response=False)
        self.assertEqual(regra.cargo, 'Auxiliar')
        pagina = self.client.get(reverse('folha:regra_programa', args=[regra.id]))
        self.assertContains(pagina, 'No topo, 1 pessoa(s) com o cargo desta regra')  # a Ana
        self.assertEqual([item['funcionario'] for item in pagina.context['candidatos']], [self.ana, self.bia])

        self.client.post(reverse('folha:incluir_no_programa', args=[regra.id]), {'funcionario': [self.ana.id, self.bia.id]})

        self.assertEqual({membro.funcionario for membro in regra.membros.all()}, {self.ana, self.bia})
        fechamento = services.abrir_fechamento(AGOSTO, None)
        self.assertEqual(
            {linha.funcionario: linha.premio_programa for linha in fechamento.linhas.all()},
            {self.ana: Decimal('100'), self.bia: Decimal('100')},
        )

    def test_colocar_quem_esta_em_outra_regra_muda_de_regra(self):
        auxiliar = _regra_programa('100', self.ana)
        operador = _regra_programa('300', self.bia, cargo='Operador')

        resposta = self.client.post(reverse('folha:incluir_no_programa', args=[operador.id]), {'funcionario': self.ana.id}, follow=True)

        self.assertEqual(MembroPremioPrograma.objects.get(funcionario=self.ana).regra, operador)
        self.assertFalse(auxiliar.membros.exists())
        self.assertContains(resposta, 'saiu de Auxiliar')

    def test_editar_tirar_e_excluir(self):
        regra = _regra_programa('100', self.ana, self.bia)

        self.client.post(reverse('folha:regra_programa', args=[regra.id]), {'cargo': 'Auxiliar', 'valor': '120'})
        regra.refresh_from_db()
        self.assertEqual(regra.valor, Decimal('120'))

        self.client.post(reverse('folha:remover_do_programa', args=[regra.id, self.ana.id]))
        self.assertEqual([membro.funcionario for membro in regra.membros.all()], [self.bia])

        resposta = self.client.post(reverse('folha:excluir_regra_programa', args=[regra.id]))
        self.assertRedirects(resposta, reverse('folha:premios') + '#premio_programa', fetch_redirect_response=False)
        self.assertFalse(RegraPremioPrograma.objects.exists())
        self.assertFalse(MembroPremioPrograma.objects.exists())

    def test_nao_cria_regra_repetida(self):
        _regra_programa('100')

        resposta = self.client.post(reverse('folha:criar_regra_programa'), {'cargo': 'AUXILIAR', 'valor': '100.00'})

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Já existe a regra')
        self.assertEqual(RegraPremioPrograma.objects.count(), 1)

    def test_pagina_de_premios_mostra_as_regras_e_quem_esta_sem(self):
        regra = _regra_programa('100', self.bia, cargo='Auxiliar')

        pagina = self.client.get(reverse('folha:premios'))

        self.assertContains(pagina, 'Ativos sem regra: 1 pessoa(s), 1 com cargo que já tem regra')  # a Ana
        self.assertContains(pagina, reverse('folha:regra_programa', args=[regra.id]))
        resposta = self.client.post(
            reverse('folha:incluir_no_programa', args=[regra.id]), {'funcionario': self.ana.id, 'voltar': 'premios'},
        )
        self.assertRedirects(resposta, reverse('folha:premios') + '#premio_programa', fetch_redirect_response=False)
        self.assertEqual(self.ana.membro_premio_programa.regra, regra)

    def test_conferencia_com_o_ultimo_fechamento_ignora_as_reducoes(self):
        _regra_programa('300', self.ana)
        caio = Funcionario.objects.create(nome='Caio', matricula='3', cargo='Auxiliar', setor=self.setor, cpf='33333333333')
        _regra_programa('100', caio, cargo='Ajudante')
        julho = Fechamento.objects.create(competencia=JULHO, status='fechado')
        for funcionario, valor in ((self.ana, '150'), (self.bia, '200'), (caio, '120')):
            LinhaFechamento.objects.create(fechamento=julho, funcionario=funcionario, nome=funcionario.nome, premio_programa=Decimal(valor))

        pagina = self.client.get(reverse('folha:premios'))

        # A Ana recebeu menos que a regra (redução do mês): normal. A Bia recebeu sem regra e o Caio, acima dela.
        self.assertEqual([item['linha'].funcionario for item in pagina.context['programa']['diferencas']], [self.bia, caio])
        self.assertContains(pagina, 'Fora das regras em')


class PremioSafraTests(TestCase):
    """Safra: % do salário nos meses da safra, com a lista de quem recebe (pedido do usuário em 29/10/2026)."""

    def setUp(self):
        moega = Setor.objects.create(nome='Moega')
        self.ana = Funcionario.objects.create(nome='Ana', matricula='1', cargo='Auxiliar', setor=moega, cpf='11111111111')
        self.bia = Funcionario.objects.create(nome='Bia', matricula='2', cargo='Auxiliar', setor=moega, cpf='22222222222')

    def test_so_recebe_quem_esta_na_lista_e_so_nos_meses_da_safra(self):
        _regra_safra(self.bia, de=date(2026, 9, 1), ate=date(2026, 12, 1))

        setembro = premios.RegrasDoMes(date(2026, 9, 1))
        self.assertEqual(setembro.valores_para(self.bia)['premio_safra'], Decimal('10'))
        self.assertIsNone(setembro.valores_para(self.ana)['premio_safra'])  # mesmo setor, mas fora da lista
        self.assertIsNone(premios.RegrasDoMes(date(2026, 8, 1)).valores_para(self.bia)['premio_safra'])
        self.assertIsNone(premios.RegrasDoMes(date(2027, 1, 1)).valores_para(self.bia)['premio_safra'])

    def test_cada_pessoa_fica_em_uma_regra_so(self):
        _regra_safra(self.ana)
        outra = _regra_safra(nome='Safra 2027')
        with self.assertRaises(IntegrityError), transaction.atomic():
            MembroPremioSafra.objects.create(regra=outra, funcionario=self.ana)

    def test_ultimo_mes_nao_pode_ser_antes_do_primeiro(self):
        regra = RegraPremioSafra(nome='Safra', vigente_de=date(2026, 9, 1), vigente_ate=date(2026, 8, 1))
        with self.assertRaises(ValidationError):
            regra.full_clean()


class PremioSafraViewsTests(BaseFolhaTestCase):
    def setUp(self):
        super().setUp()
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9999', cargo='RH', setor=self.setor, cpf='99999999999', status='inativo')
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.login(username='rhteste', password='senha123')
        self.moega = Setor.objects.create(nome='Moega')
        self.bia = Funcionario.objects.create(nome='Bia', matricula='2', cargo='Operador', setor=self.moega, cpf='22222222222')

    def test_criar_regra_colocar_pessoas_e_o_fechamento_usar(self):
        resposta = self.client.post(reverse('folha:criar_regra_safra'), {
            'nome': ' Safra  2026 ', 'percentual': '10', 'vigente_de': '2026-08-20', 'vigente_ate': '2026-12-31',
        })

        regra = RegraPremioSafra.objects.get()
        self.assertRedirects(resposta, reverse('folha:regra_safra', args=[regra.id]), fetch_redirect_response=False)
        self.assertEqual((regra.nome, regra.vigente_de, regra.vigente_ate), ('Safra 2026', date(2026, 8, 1), date(2026, 12, 1)))

        self.client.post(reverse('folha:incluir_na_safra', args=[regra.id]), {'funcionario': [self.bia.id]})

        caio = Funcionario.objects.create(nome='Caio', matricula='3', cargo='Auxiliar', setor=self.moega, cpf='33333333333')
        pagina = self.client.get(reverse('folha:regra_safra', args=[regra.id]))
        self.assertContains(pagina, 'No topo, 1 pessoa(s) dos setores desta regra')  # o Caio, da Moega como a Bia
        self.assertEqual([item['funcionario'] for item in pagina.context['candidatos']], [caio, self.ana])
        fechamento = services.abrir_fechamento(AGOSTO, None)
        self.assertEqual(
            {linha.funcionario: linha.premio_safra for linha in fechamento.linhas.all()},
            {self.ana: None, self.bia: Decimal('10'), caio: None},
        )

    def test_colocar_quem_esta_em_outra_regra_muda_de_regra(self):
        antiga = _regra_safra(self.ana, nome='Safra 2025')
        nova = _regra_safra(nome='Safra 2026')

        resposta = self.client.post(reverse('folha:incluir_na_safra', args=[nova.id]), {'funcionario': self.ana.id}, follow=True)

        self.assertEqual(MembroPremioSafra.objects.get(funcionario=self.ana).regra, nova)
        self.assertFalse(antiga.membros.exists())
        self.assertContains(resposta, 'saiu de Safra 2025')

    def test_editar_tirar_e_excluir(self):
        regra = _regra_safra(self.ana, self.bia)

        self.client.post(reverse('folha:regra_safra', args=[regra.id]), {'nome': 'Safra 2026', 'percentual': '12.5'})
        regra.refresh_from_db()
        self.assertEqual(regra.percentual, Decimal('12.5'))

        self.client.post(reverse('folha:remover_da_safra', args=[regra.id, self.ana.id]))
        self.assertEqual([membro.funcionario for membro in regra.membros.all()], [self.bia])

        resposta = self.client.post(reverse('folha:excluir_regra_safra', args=[regra.id]))
        self.assertRedirects(resposta, reverse('folha:premios') + '#premio_safra', fetch_redirect_response=False)
        self.assertFalse(RegraPremioSafra.objects.exists())
        self.assertFalse(MembroPremioSafra.objects.exists())

    def test_meses_invertidos_nao_salvam(self):
        resposta = self.client.post(reverse('folha:criar_regra_safra'), {
            'nome': 'Safra', 'percentual': '10', 'vigente_de': '2026-12-01', 'vigente_ate': '2026-09-01',
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'O último mês não pode ser antes do primeiro.')
        self.assertFalse(RegraPremioSafra.objects.exists())

    def test_pagina_de_premios_mostra_quem_recebeu_sem_regra(self):
        regra = _regra_safra(self.bia, de=date(2026, 9, 1))
        setembro = Fechamento.objects.create(competencia=date(2026, 9, 1), status='fechado')
        for funcionario in (self.ana, self.bia):
            LinhaFechamento.objects.create(
                fechamento=setembro, funcionario=funcionario, nome=funcionario.nome, premio_safra=Decimal('10'),
            )

        pagina = self.client.get(reverse('folha:premios'))

        self.assertEqual([item['linha'].funcionario for item in pagina.context['safra']['receberam_sem_regra']], [self.ana])
        self.assertContains(pagina, reverse('folha:regra_safra', args=[regra.id]))
        resposta = self.client.post(
            reverse('folha:incluir_na_safra', args=[regra.id]), {'funcionario': self.ana.id, 'voltar': 'premios'},
        )
        self.assertRedirects(resposta, reverse('folha:premios') + '#premio_safra', fetch_redirect_response=False)
        self.assertEqual(MembroPremioSafra.objects.get(funcionario=self.ana).regra, regra)


class MigracaoDasRegrasDeSafraTests(TestCase):
    """As regras de safra do modelo antigo (setor, cargo ou pessoa) viram regras com lista (migração 0004)."""

    def test_quem_a_regra_atingia_entra_na_lista(self):
        moega, pcp = Setor.objects.create(nome='Moega'), Setor.objects.create(nome='PCP')
        Funcionario.objects.create(nome='Ana', matricula='1', cargo='Auxiliar', setor=moega, cpf='11111111111')
        bia = Funcionario.objects.create(nome='Bia', matricula='2', cargo='Auxiliar', setor=moega, cpf='22222222222')
        caio = Funcionario.objects.create(nome='Caio', matricula='3', cargo='Analista', setor=pcp, cpf='33333333333')
        Funcionario.objects.create(nome='Dani', matricula='4', cargo='Auxiliar', setor=moega, cpf='44444444444', status='inativo')
        safra = {'vigente_de': date(2026, 9, 1), 'vigente_ate': date(2026, 12, 1)}
        RegraPremio.objects.create(tipo='premio_safra', setor=moega, valor=Decimal('10'), **safra)
        RegraPremio.objects.create(tipo='premio_safra', funcionario=bia, valor=Decimal('0'), **safra)  # não recebe
        RegraPremio.objects.create(tipo='premio_safra', funcionario=caio, valor=Decimal('12'))
        RegraPremio.objects.create(tipo='premio_incentivo', setor=moega, valor=Decimal('100'))

        migracao = importlib.import_module('folha.migrations.0004_premio_safra_com_lista')
        migracao.converter_regras_de_safra(django_apps, None)

        self.assertEqual(
            {
                (regra.nome, regra.percentual, regra.vigente_de, regra.vigente_ate, tuple(m.funcionario.nome for m in regra.membros.all()))
                for regra in RegraPremioSafra.objects.all()
            },
            {
                ('Moega', Decimal('10'), date(2026, 9, 1), date(2026, 12, 1), ('Ana',)),
                ('Caio', Decimal('12'), None, None, ('Caio',)),
            },
        )
        self.assertEqual(list(RegraPremio.objects.values_list('tipo', flat=True)), ['premio_incentivo'])


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
        _regra_programa('100', self.ana)
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


class GestorNaFolhaTests(BaseFolhaTestCase):
    """O gestor lança e corrige a folha como o RH, mas só as linhas da equipe dele."""

    def setUp(self):
        super().setUp()
        self.gestor = Funcionario.objects.create(
            nome='Gil Gestor', matricula='2001', cargo='Gerente', setor=self.setor, cpf='22200000000',
        )
        self.setor.gestor = self.gestor
        self.setor.save()
        user = User.objects.create_user(username='gilgestor', password='senha123')
        Usuario.objects.create(user=user, funcionario=self.gestor, papel='gestor')
        self.client.login(username='gilgestor', password='senha123')
        self.bruno = Funcionario.objects.create(
            nome='Bruno Financeiro', matricula='2002', cargo='Analista', setor=Setor.objects.create(nome='Financeiro'),
            cpf='22200000001',
        )
        self.fechamento = self._abrir_calculado()  # Ana (equipe), o gestor e o Bruno (de fora)
        self.linha_ana = self.fechamento.linhas.get(funcionario=self.ana)
        self.linha_bruno = self.fechamento.linhas.get(funcionario=self.bruno)
        self.linha_gestor = self.fechamento.linhas.get(funcionario=self.gestor)

    def _salvar(self, linha, campo='premio_bonus', valor='150,00'):
        return self.client.post(reverse('folha:salvar_celula', args=[linha.id]), {'campo': campo, 'valor': valor})

    def test_lista_conta_so_a_equipe_e_nao_abre_competencia(self):
        resposta = self.client.get(reverse('folha:listar'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['fechamentos'].get(id=self.fechamento.id).total_linhas, 1)
        self.assertNotContains(resposta, reverse('folha:abrir'))
        self.assertNotContains(resposta, reverse('folha:exportar_xlsx', args=[self.fechamento.id]))
        self.assertNotContains(resposta, reverse('folha:premios'))

    def test_grade_so_com_a_equipe_e_sem_acoes_da_empresa_toda(self):
        resposta = self.client.get(reverse('folha:detalhe', args=[self.fechamento.id]))

        self.assertEqual([item['linha'] for item in resposta.context['grade']], [self.linha_ana])
        self.assertEqual(resposta.context['fora_do_fechamento'], [])
        for nome in ('fechar', 'excluir', 'exportar_xlsx', 'exportar_pdf'):
            self.assertNotContains(resposta, reverse(f'folha:{nome}', args=[self.fechamento.id]))
        self.assertContains(resposta, 'Recalcular ponto da equipe')

    def test_lanca_valor_so_na_linha_da_equipe(self):
        self.assertEqual(self._salvar(self.linha_ana).status_code, 200)
        self.assertEqual(self._salvar(self.linha_bruno).status_code, 404)
        self.assertEqual(self._salvar(self.linha_gestor).status_code, 404)  # nem a própria

        self.linha_ana.refresh_from_db()
        self.linha_bruno.refresh_from_db()
        self.assertEqual(self.linha_ana.premio_bonus, Decimal('150.00'))
        self.assertIsNone(self.linha_bruno.premio_bonus)

    def test_reduz_o_premio_programa_da_equipe_e_o_recalculo_mantem(self):
        _regra_programa('300', self.ana)
        services.recalcular_linha(self.linha_ana)

        self.assertEqual(self._salvar(self.linha_ana, 'premio_programa', '150,00').status_code, 200)
        self.client.post(reverse('folha:recalcular_linha', args=[self.linha_ana.id]))

        self.linha_ana.refresh_from_db()
        self.assertEqual(self.linha_ana.premio_programa, Decimal('150.00'))
        self.assertTrue(self.linha_ana.editado_pelo_rh('premio_programa'))

    def test_recalcula_so_a_equipe(self):
        self.client.post(reverse('folha:recalcular', args=[self.fechamento.id]))

        self.assertEqual(list(self.fechamento.linhas_pendentes()), [self.linha_ana])
        lote = self.client.post(reverse('folha:calcular_lote', args=[self.fechamento.id])).json()
        self.assertEqual(lote, {'restantes': 0, 'total': 1})
        self.assertEqual(self.client.post(reverse('folha:recalcular_linha', args=[self.linha_bruno.id])).status_code, 404)

    def test_remove_e_inclui_so_quem_e_da_equipe(self):
        self.assertEqual(self.client.post(reverse('folha:remover_linha', args=[self.linha_bruno.id])).status_code, 404)
        self.client.post(reverse('folha:remover_linha', args=[self.linha_ana.id]))
        self.linha_bruno.delete()  # o RH tirou o Bruno

        self.client.post(reverse('folha:incluir_funcionario', args=[self.fechamento.id]), {'funcionario': self.bruno.id})
        self.client.post(reverse('folha:incluir_funcionario', args=[self.fechamento.id]), {'funcionario': self.ana.id})

        self.assertCountEqual(
            self.fechamento.linhas.values_list('funcionario', flat=True), [self.ana.id, self.gestor.id],
        )

    def test_acoes_da_empresa_toda_continuam_so_com_o_rh(self):
        id_ = self.fechamento.id
        pedidos = [
            ('post', reverse('folha:abrir'), {'competencia': '2026-07-01'}),
            ('post', reverse('folha:fechar', args=[id_]), {}),
            ('post', reverse('folha:excluir', args=[id_]), {}),
            ('get', reverse('folha:exportar_xlsx', args=[id_]), {}),
            ('get', reverse('folha:exportar_pdf', args=[id_]), {}),
            ('get', reverse('folha:premios'), {}),
        ]
        regra = _regra_programa('100')
        pedidos += [
            ('get', reverse('folha:regra_programa', args=[regra.id]), {}),
            ('post', reverse('folha:criar_regra_programa'), {'cargo': 'Gerente', 'valor': '900'}),
            ('post', reverse('folha:incluir_no_programa', args=[regra.id]), {'funcionario': self.gestor.id}),
            ('post', reverse('folha:excluir_regra_programa', args=[regra.id]), {}),
        ]
        safra = _regra_safra()
        pedidos += [
            ('get', reverse('folha:regra_safra', args=[safra.id]), {}),
            ('post', reverse('folha:criar_regra_safra'), {'nome': 'Safra', 'percentual': '10'}),
            ('post', reverse('folha:incluir_na_safra', args=[safra.id]), {'funcionario': self.gestor.id}),
            ('post', reverse('folha:excluir_regra_safra', args=[safra.id]), {}),
        ]
        for metodo, url, dados in pedidos:
            with self.subTest(url=url):
                resposta = getattr(self.client, metodo)(url, dados)
                self.assertRedirects(resposta, reverse('dashboard'), fetch_redirect_response=False)
        self.fechamento.refresh_from_db()
        self.assertTrue(self.fechamento.aberto)
        self.assertFalse(Fechamento.objects.filter(competencia=JULHO).exists())
        self.assertEqual(list(RegraPremioPrograma.objects.all()), [regra])
        self.assertFalse(MembroPremioPrograma.objects.exists())
        self.assertEqual(list(RegraPremioSafra.objects.all()), [safra])
        self.assertFalse(MembroPremioSafra.objects.exists())
