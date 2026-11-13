"""
Quem é DP/RH não altera o próprio ponto, atestado, jornada, folha e cadastro,
nem os de outro DP/RH: pede a correção ao gerente do RH (gestor com acesso de
RH), que altera a si mesmo e a todos (decisão do usuário em 13/11/2026).
A linha da folha está em `folha.tests.LinhaDeQuemEDpRhTests`.
"""
from datetime import date, time
from decimal import Decimal

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from ponto.models import AjustePonto, FuncionarioJornada

from .models import MENSAGEM_ALTERACAO_RESTRITA, Atestado, Funcionario, Setor, Usuario


class AlteracaoRestritaTests(TestCase):
    def setUp(self):
        self.producao = Setor.objects.create(nome='Produção')
        self.rh_setor = Setor.objects.create(nome='Recursos Humanos')

        self.gerente = self._conta('10000000001', 'gestor', self.rh_setor, acesso_rh=True)
        self.dp = self._conta('10000000002', 'dp', self.rh_setor)
        self.rh = self._conta('10000000003', 'rh', self.rh_setor)
        self.rh_por_acesso = self._conta('10000000004', 'colaborador', self.rh_setor, acesso_rh=True)
        self.gestor = self._conta('10000000005', 'gestor', self.producao)
        self.colaborador = self._conta('20000000001', 'colaborador', self.producao)
        self.dp_na_producao = self._conta('20000000002', 'dp', self.producao)

    def _conta(self, cpf, papel, setor, acesso_rh=False):
        funcionario = Funcionario.objects.create(
            nome=f'Pessoa {cpf}', cpf=cpf, cargo='Cargo', setor=setor, status='ativo', salario=Decimal('2000.00'),
        )
        user = User.objects.create_user(username=cpf, password='senha-forte-123')
        return Usuario.objects.create(user=user, funcionario=funcionario, papel=papel, acesso_rh=acesso_rh)

    def _confere(self, quem, permitidos, negados):
        for alvo in permitidos:
            with self.subTest(quem=quem.user.username, alvo=alvo.user.username):
                self.assertTrue(quem.pode_alterar_ponto_e_folha_de(alvo.funcionario))
        for alvo in negados:
            with self.subTest(quem=quem.user.username, alvo=alvo.user.username):
                self.assertFalse(quem.pode_alterar_ponto_e_folha_de(alvo.funcionario))

    def _mensagens(self, resposta):
        return [str(m) for m in get_messages(resposta.wsgi_request)]

    # ----- a regra -----

    def test_gerente_do_rh_altera_a_si_mesmo_e_a_todos(self):
        self._confere(self.gerente, [self.gerente, self.dp, self.rh, self.rh_por_acesso, self.colaborador], [])

    def test_dp_e_rh_nao_alteram_a_si_nem_outro_dp_rh(self):
        for quem in (self.dp, self.rh, self.rh_por_acesso):
            outros_dp_rh = [c for c in (self.dp, self.rh, self.rh_por_acesso, self.dp_na_producao) if c != quem]
            self._confere(quem, [self.colaborador], [quem, *outros_dp_rh])

    def test_dp_continua_alterando_o_gerente(self):
        self._confere(self.dp, [self.gerente], [])

    def test_gestor_comum_nao_altera_dp_rh_da_equipe(self):
        self._confere(self.gestor, [self.colaborador], [self.dp_na_producao, self.gestor])

    # ----- ponto -----

    def _ajuste(self, conta):
        return AjustePonto.objects.create(
            funcionario=conta.funcionario, data=date(2026, 11, 10), tipo_evento='entrada',
            horario_ajustado=time(8, 0), motivo='Esqueci de bater', usuario=conta.user, solicitado_por=conta.user,
        )

    def test_dp_nao_aprova_o_proprio_pedido_de_ajuste(self):
        ajuste = self._ajuste(self.dp)
        self.client.force_login(self.dp.user)

        resposta = self.client.post(reverse('ponto:aprovar_ajuste', args=[ajuste.id]))

        ajuste.refresh_from_db()
        self.assertEqual(ajuste.status, 'pendente')
        self.assertEqual(self._mensagens(resposta), [MENSAGEM_ALTERACAO_RESTRITA])

    def test_gerente_do_rh_aprova_o_pedido_do_dp(self):
        ajuste = self._ajuste(self.dp)
        self.client.force_login(self.gerente.user)

        self.client.post(reverse('ponto:aprovar_ajuste', args=[ajuste.id]))

        ajuste.refresh_from_db()
        self.assertEqual(ajuste.status, 'aprovado')
        self.assertEqual(ajuste.analisado_por, self.gerente.user)

    def test_fila_mostra_o_proprio_pedido_sem_os_botoes(self):
        proprio = self._ajuste(self.dp)
        do_colaborador = self._ajuste(self.colaborador)
        self.client.force_login(self.dp.user)

        resposta = self.client.get(reverse('ponto:ajustes_pendentes'))

        self.assertContains(resposta, 'Aguarda o gerente do RH')
        self.assertNotContains(resposta, reverse('ponto:aprovar_ajuste', args=[proprio.id]))
        self.assertContains(resposta, reverse('ponto:aprovar_ajuste', args=[do_colaborador.id]))

    def test_dp_nao_ajusta_a_propria_batida_no_espelho(self):
        self.client.force_login(self.dp.user)
        url = reverse('ponto:ajustar_marcacao', args=[self.dp.funcionario_id, '2026-11-10'])

        resposta = self.client.get(url)

        self.assertEqual(resposta.status_code, 302)
        self.assertEqual(self._mensagens(resposta), [MENSAGEM_ALTERACAO_RESTRITA])

    def test_gerente_do_rh_ajusta_a_propria_batida(self):
        self.client.force_login(self.gerente.user)

        resposta = self.client.get(reverse('ponto:ajustar_marcacao', args=[self.gerente.funcionario_id, '2026-11-10']))

        self.assertEqual(resposta.status_code, 200)

    def test_dp_nao_troca_a_propria_jornada(self):
        self.client.force_login(self.dp.user)

        resposta = self.client.post(
            reverse('ponto:atribuir_jornada', args=[self.dp.funcionario_id]), {'data_inicio': '2026-11-01'},
        )

        self.assertFalse(FuncionarioJornada.objects.filter(funcionario=self.dp.funcionario).exists())
        self.assertEqual(self._mensagens(resposta), [MENSAGEM_ALTERACAO_RESTRITA])

    # ----- atestado e cadastro -----

    def test_dp_nao_lanca_atestado_para_si(self):
        self.client.force_login(self.dp.user)

        resposta = self.client.post(
            reverse('lancar_atestado', args=[self.dp.funcionario_id]),
            {'data_inicio': '2026-11-10', 'data_fim': '2026-11-12'},
        )

        self.assertFalse(Atestado.objects.exists())
        self.assertEqual(self._mensagens(resposta), [MENSAGEM_ALTERACAO_RESTRITA])

    def test_dp_nao_altera_o_proprio_salario(self):
        self.client.force_login(self.dp.user)

        self.client.post(reverse('editar_dados_funcionario', args=[self.dp.funcionario_id]), {'salario': '9999,00'})

        self.dp.funcionario.refresh_from_db()
        self.assertEqual(self.dp.funcionario.salario, Decimal('2000.00'))

    def test_rh_nao_altera_o_salario_de_outro_dp(self):
        self.client.force_login(self.rh.user)

        resposta = self.client.post(
            reverse('editar_dados_funcionario', args=[self.dp.funcionario_id]), {'salario': '9999,00'},
        )

        self.dp.funcionario.refresh_from_db()
        self.assertEqual(self.dp.funcionario.salario, Decimal('2000.00'))
        self.assertEqual(self._mensagens(resposta), [MENSAGEM_ALTERACAO_RESTRITA])
