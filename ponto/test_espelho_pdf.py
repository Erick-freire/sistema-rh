"""Espelho de ponto em PDF (ponto/espelho_pdf.py e a tela Espelho em PDF)."""
from datetime import date, datetime, time
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from funcionarios.models import Funcionario, Setor, Usuario

from . import espelho_pdf
from .forms import ExportarEspelhoForm
from .models import AjustePonto, Equipamento, FuncionarioJornada, Horario, HorarioDia, MarcacaoRelogio

SETEMBRO = (date(2026, 9, 1), date(2026, 9, 30))


def _horario_util(numero):
    horario = Horario.objects.create(numero=numero, nome=f'Jornada {numero}')
    for dia in range(1, 6):
        HorarioDia.objects.create(horario=horario, dia_semana=dia, entrada1='08:00', saida1='12:00', entrada2='13:00', saida2='17:00')
    for dia in (6, 7):
        HorarioDia.objects.create(horario=horario, dia_semana=dia)
    return horario


def _bater(funcionario, equipamento, momentos):
    for i, momento in enumerate(momentos):
        MarcacaoRelogio.objects.create(
            equipamento=equipamento, funcionario=funcionario, id_evento_dispositivo=f'{funcionario.pk}-{i}',
            momento=timezone.make_aware(momento),
        )


class BaseEspelhoTestCase(TestCase):
    def setUp(self):
        self.producao = Setor.objects.create(nome='Produção')
        self.equipamento = Equipamento.objects.create(nome='Relógio', ip='192.168.1.50', usuario_admin='admin', senha_admin='admin')
        self.ana = Funcionario.objects.create(nome='Ana Souza', matricula='1435', cargo='Auxiliar', setor=self.producao, cpf='11111111111')
        self.bia = Funcionario.objects.create(nome='Bia Lima', matricula='1500', cargo='Auxiliar', setor=self.producao, cpf='22222222222')
        horario = _horario_util(7101)
        for funcionario in (self.ana, self.bia):
            FuncionarioJornada.objects.create(funcionario=funcionario, data_inicio=date(2026, 8, 1), horario=horario)


class DadosDoEspelhoTests(BaseEspelhoTestCase):
    def test_batidas_totais_e_observacoes(self):
        # 01/09 (terça): 1h a mais, com a saída lançada à mão pelo RH.
        _bater(self.ana, self.equipamento, [datetime(2026, 9, 1, 8), datetime(2026, 9, 1, 12), datetime(2026, 9, 1, 13)])
        AjustePonto.objects.create(
            funcionario=self.ana, data=date(2026, 9, 1), tipo_evento='saida', horario_ajustado=time(18), motivo='Esqueceu',
            status='aprovado',
        )

        espelho = espelho_pdf.dados_do_espelho(self.ana, date(2026, 9, 1), date(2026, 9, 6))

        primeiro = espelho['dias'][0]
        self.assertEqual(primeiro['data'], '01/09 ter')
        self.assertEqual(primeiro['batidas'], ['08:00', '12:00', '13:00', '18:00*', '', ''])
        self.assertEqual(primeiro['extra_50'], '01:00')
        self.assertEqual(espelho['dias'][1]['faltas'], 'Dia falta')  # 02/09 sem batida
        self.assertEqual(espelho['dias'][4]['observacao'], 'Folga')  # sábado
        self.assertEqual(espelho['totais']['dias_falta'], 3)
        self.assertEqual(espelho['jornadas'][0].horario.numero, 7101)

    def test_batida_do_dia_seguinte_ganha_mais_um(self):
        horario = Horario.objects.create(numero=7102, nome='12x36 noturno', eh_12x36=True)
        for dia in range(1, 8):
            HorarioDia.objects.create(horario=horario, dia_semana=dia, entrada1='18:00', saida1='22:00', entrada2='23:00', saida2='06:00')
        FuncionarioJornada.objects.create(funcionario=self.bia, data_inicio=date(2026, 9, 1), horario=horario)
        _bater(self.bia, self.equipamento, [
            datetime(2026, 9, 1, 18), datetime(2026, 9, 1, 22), datetime(2026, 9, 1, 23), datetime(2026, 9, 2, 6),
        ])

        espelho = espelho_pdf.dados_do_espelho(self.bia, date(2026, 9, 1), date(2026, 9, 2))

        self.assertEqual(espelho['dias'][0]['batidas'], ['18:00', '22:00', '23:00', '06:00+1', '', ''])

    def test_totais_tem_noturno_no_relogio_e_convertido(self):
        horario = Horario.objects.create(numero=7103, nome='12x36 noturno', eh_12x36=True)
        for dia in range(1, 8):
            HorarioDia.objects.create(horario=horario, dia_semana=dia, entrada1='18:00', saida1='22:00', entrada2='23:00', saida2='06:00')
        FuncionarioJornada.objects.create(funcionario=self.bia, data_inicio=date(2026, 9, 1), horario=horario)
        _bater(self.bia, self.equipamento, [
            datetime(2026, 9, 1, 18), datetime(2026, 9, 1, 22), datetime(2026, 9, 1, 23), datetime(2026, 9, 2, 6),
        ])

        espelho = espelho_pdf.dados_do_espelho(self.bia, date(2026, 9, 1), date(2026, 9, 2))

        self.assertEqual(espelho['dias'][0]['noturno'], '06:00')  # 23:00-05:00
        self.assertEqual(espelho['totais']['noturno'], '06:00')
        self.assertEqual(espelho['totais']['noturno_convertido'], '06:51')

    def test_setor_sem_gestores_nem_inativos(self):
        gestor = Funcionario.objects.create(nome='Gestor', matricula='9000', cargo='Gerente', setor=self.producao, cpf='33333333333')
        Usuario.objects.create(user=User.objects.create_user(username='gestor'), funcionario=gestor, papel='gestor')
        Funcionario.objects.create(nome='Saiu', matricula='9001', cargo='X', setor=self.producao, cpf='44444444444', status='inativo')

        self.assertEqual(list(espelho_pdf.funcionarios_do_setor(self.producao)), [self.ana, self.bia])


class ExportarEspelhoFormTests(BaseEspelhoTestCase):
    def _form(self, **dados):
        padrao = {'data_inicio': '2026-09-01', 'data_fim': '2026-09-30', 'filtro': 'setor', 'setor': self.producao.id}
        padrao.update(dados)
        return ExportarEspelhoForm(padrao)

    def test_setor_traz_todos_do_setor(self):
        form = self._form()
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['funcionarios'], [self.ana, self.bia])

    def test_funcionario_traz_so_ele(self):
        form = self._form(filtro='funcionario', funcionario=self.bia.id)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['funcionarios'], [self.bia])

    def test_validacoes(self):
        self.assertIn('data_fim', self._form(data_fim='2026-08-01').errors)
        self.assertIn('setor', self._form(setor='').errors)
        self.assertIn('funcionario', self._form(filtro='funcionario').errors)
        vazio = Setor.objects.create(nome='Vazio')
        self.assertIn('setor', self._form(setor=vazio.id).errors)

    def test_setor_inteiro_em_periodo_longo_demais(self):
        with patch('ponto.forms.LIMITE_PESSOAS_DIA_ESPELHO', 50):
            form = self._form()  # 2 pessoas x 30 dias = 60
            self.assertFalse(form.is_valid())
            self.assertIn('até 25 dias', ' '.join(form.non_field_errors()))
            # Um funcionário só, no mesmo período, passa.
            self.assertTrue(self._form(filtro='funcionario', funcionario=self.ana.id).is_valid())


class ExportarEspelhoViewTests(BaseEspelhoTestCase):
    def setUp(self):
        super().setUp()
        rh = Funcionario.objects.create(nome='Rh Teste', matricula='9999', cargo='RH', setor=self.producao, cpf='99999999999', status='inativo')
        user = User.objects.create_user(username='rhteste', password='senha123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.login(username='rhteste', password='senha123')

    def test_filtro_fica_na_tela_espelho_e_calculo(self):
        resposta = self.client.get(reverse('ponto:listar_calculo'), {
            'funcionario': self.ana.id, 'data_inicio': '2026-09-01', 'data_fim': '2026-09-30',
        })

        self.assertContains(resposta, f'formaction="{reverse("ponto:exportar_espelho")}"')
        self.assertContains(resposta, 'Produção (2)')  # quantos entram no espelho do setor
        self.assertContains(resposta, 'value="2026-09-01"')
        self.assertIsNotNone(resposta.context['resultado'])  # o Calcular continua mostrando o espelho

    def test_com_setor_escolhido_a_tela_nao_calcula_ninguem(self):
        resposta = self.client.get(reverse('ponto:listar_calculo'), {
            'filtro': 'setor', 'setor': self.producao.id, 'funcionario': self.ana.id,
        })

        self.assertIsNone(resposta.context['resultado'])

    def test_gera_pdf_do_setor(self):
        resposta = self.client.get(reverse('ponto:exportar_espelho'), {
            'filtro': 'setor', 'setor': self.producao.id, 'data_inicio': '2026-09-01', 'data_fim': '2026-09-30',
        })

        self.assertEqual(resposta['Content-Type'], 'application/pdf')
        self.assertIn('espelho_ponto_producao_01-09-2026_a_30-09-2026.pdf', resposta['Content-Disposition'])
        self.assertTrue(resposta.content.startswith(b'%PDF'))

    def test_gera_pdf_de_um_funcionario(self):
        resposta = self.client.get(reverse('ponto:exportar_espelho'), {
            'filtro': 'funcionario', 'funcionario': self.ana.id, 'data_inicio': '2026-09-01', 'data_fim': '2026-09-15',
        })

        self.assertEqual(resposta['Content-Type'], 'application/pdf')
        self.assertIn('espelho_ponto_ana-souza_', resposta['Content-Disposition'])

    def test_filtro_incompleto_volta_para_a_tela_com_o_motivo(self):
        parametros = {'filtro': 'funcionario', 'data_inicio': '2026-09-01', 'data_fim': '2026-09-30'}

        resposta = self.client.get(reverse('ponto:exportar_espelho'), parametros, follow=True)

        self.assertEqual(resposta.redirect_chain[0][0].split('?')[0], reverse('ponto:listar_calculo'))
        self.assertContains(resposta, 'Escolha o funcionário.')
        self.assertContains(resposta, 'value="2026-09-01"')  # o filtro volta como estava

    def test_colaborador_nao_acessa(self):
        user = User.objects.create_user(username='colab', password='senha123')
        Usuario.objects.create(user=user, funcionario=self.ana, papel='colaborador')
        self.client.login(username='colab', password='senha123')

        resposta = self.client.get(reverse('ponto:exportar_espelho'))

        self.assertRedirects(resposta, reverse('dashboard'), fetch_redirect_response=False)
