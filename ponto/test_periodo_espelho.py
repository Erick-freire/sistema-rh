"""
Espelho e cálculo (tela e PDF): período de até 5 anos (decisão de 13/11/2026).
Acima disso, avisa e não calcula. Data malformada na URL volta para o padrão,
sem erro 500.
"""
from datetime import date
from unittest import mock

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from funcionarios.models import Funcionario, Setor, Usuario

from .forms import MENSAGEM_PERIODO_LONGO, periodo_longo_demais


class PeriodoLongoDemaisTests(SimpleTestCase):
    def test_ate_cinco_anos_de_calendario(self):
        self.assertFalse(periodo_longo_demais(date(2021, 11, 13), date(2026, 11, 13)))
        self.assertTrue(periodo_longo_demais(date(2021, 11, 12), date(2026, 11, 13)))
        self.assertFalse(periodo_longo_demais(date(2026, 11, 1), date(2026, 11, 30)))

    def test_fim_em_29_de_fevereiro(self):
        self.assertFalse(periodo_longo_demais(date(2023, 2, 28), date(2028, 2, 29)))
        self.assertTrue(periodo_longo_demais(date(2023, 2, 27), date(2028, 2, 29)))

    def test_datas_absurdas_nao_quebram(self):
        self.assertTrue(periodo_longo_demais(date(1, 1, 1), date(9999, 12, 31)))
        self.assertFalse(periodo_longo_demais(date(1, 1, 1), date(3, 1, 1)))


class PeriodoDoEspelhoViewTests(TestCase):
    def setUp(self):
        setor = Setor.objects.create(nome='Produção')
        rh = Funcionario.objects.create(nome='Rh Teste', cpf='10000000001', cargo='RH', setor=setor, status='ativo')
        user = User.objects.create_user(username='10000000001', password='senha-forte-123')
        Usuario.objects.create(user=user, funcionario=rh, papel='rh')
        self.client.force_login(user)
        self.setor = setor
        self.funcionario = Funcionario.objects.create(
            nome='Ana Silva', cpf='10000000002', cargo='Operadora', setor=setor, status='ativo',
        )

    def _mensagens(self, resposta):
        return [str(m) for m in get_messages(resposta.wsgi_request)]

    def _tela(self, **parametros):
        return self.client.get(reverse('ponto:listar_calculo'), {'funcionario': self.funcionario.id, **parametros})

    def test_periodo_normal_calcula(self):
        resposta = self._tela(data_inicio='2026-10-01', data_fim='2026-10-31')

        self.assertEqual(resposta.status_code, 200)
        self.assertIsNotNone(resposta.context['resultado'])

    def test_acima_de_cinco_anos_avisa_e_nao_calcula(self):
        for filtro, extra in (('funcionario', {}), ('setor', {'setor': self.setor.id})):
            with self.subTest(filtro=filtro), mock.patch('ponto.views.calcular_periodo') as calcular:
                resposta = self._tela(data_inicio='0001-01-01', data_fim='9999-12-31', filtro=filtro, **extra)

                self.assertEqual(resposta.status_code, 200)
                calcular.assert_not_called()
                self.assertIsNone(resposta.context['resultado'])
                self.assertIsNone(resposta.context['espelho_setor'])
                self.assertIn(MENSAGEM_PERIODO_LONGO, self._mensagens(resposta))
                # As datas pedidas continuam no filtro, para a pessoa ajustar.
                self.assertEqual(resposta.context['data_inicio'], '0001-01-01')

    def test_data_malformada_volta_para_o_padrao(self):
        hoje = timezone.now().date()

        resposta = self._tela(data_inicio='abc', data_fim='2026-13-45')

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['data_inicio'], hoje.replace(day=1).isoformat())
        self.assertEqual(resposta.context['data_fim'], hoje.isoformat())
        self.assertTrue(any('Data inválida' in m for m in self._mensagens(resposta)))

    def test_pdf_acima_de_cinco_anos_nao_e_gerado(self):
        parametros = {
            'filtro': 'funcionario', 'funcionario': self.funcionario.id,
            'data_inicio': '2020-01-01', 'data_fim': '2026-11-13',
        }
        with mock.patch('ponto.espelho_pdf.gerar_pdf') as gerar:
            resposta = self.client.get(reverse('ponto:exportar_espelho'), parametros, follow=True)

        gerar.assert_not_called()
        mensagens = self._mensagens(resposta)
        self.assertEqual(mensagens.count(MENSAGEM_PERIODO_LONGO), 1)  # a tela avisa uma vez, sem repetir

    def test_pdf_de_cinco_anos_e_gerado(self):
        parametros = {
            'filtro': 'funcionario', 'funcionario': self.funcionario.id,
            'data_inicio': '2021-11-13', 'data_fim': '2026-11-13',
        }
        with mock.patch('ponto.espelho_pdf.gerar_pdf', return_value=b'%PDF-1.4') as gerar:
            resposta = self.client.get(reverse('ponto:exportar_espelho'), parametros)

        self.assertEqual(resposta.status_code, 200)
        gerar.assert_called_once()
