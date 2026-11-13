"""
Redefinir senha não pode dar poder a quem redefine (`Usuario.pode_redefinir_senha_de`).

Quem redefine vê a senha nova e pode entrar como o dono da conta. Regra:
- colaborador comum: RH, DP e o gestor da equipe;
- conta com papel (gestor, DP, segurança do trabalho, RH/acesso de RH): só o RH;
- administrador do sistema (superuser/staff): ninguém pelo app, só pelo /admin/.
"""
from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse

from .models import Funcionario, Setor, SolicitacaoSenha, Usuario


class RedefinirSenhaTests(TestCase):
    def setUp(self):
        self.producao = Setor.objects.create(nome='Produção')
        self.rh_setor = Setor.objects.create(nome='Recursos Humanos')
        self.dp_setor = Setor.objects.create(nome='Departamento Pessoal')

        # Quem redefine
        self.rh = self._conta('10000000001', 'rh', self.rh_setor)
        self.rh_por_acesso = self._conta('10000000002', 'gestor', self.rh_setor, acesso_rh=True)
        self.dp = self._conta('10000000003', 'dp', self.dp_setor)
        self.gestor = self._conta('10000000004', 'gestor', self.producao)

        # De quem se redefine -- todos na equipe do gestor (Produção)
        self.colaborador = self._conta('20000000001', 'colaborador', self.producao)
        self.seguranca = self._conta('20000000002', 'seguranca_trabalho', self.producao)
        self.dp_na_producao = self._conta('20000000003', 'dp', self.producao)
        self.acesso_rh_na_producao = self._conta('20000000004', 'colaborador', self.producao, acesso_rh=True)
        self.superuser = self._conta('20000000005', 'colaborador', self.producao, superuser=True)
        self.staff = self._conta('20000000006', 'colaborador', self.producao, staff=True)
        self.outro_rh = self._conta('20000000007', 'rh', self.rh_setor)

    def _conta(self, cpf, papel, setor, acesso_rh=False, superuser=False, staff=False):
        funcionario = Funcionario.objects.create(
            nome=f'Pessoa {cpf}', cpf=cpf, cargo='Cargo', setor=setor, status='ativo',
        )
        user = User.objects.create_user(
            username=cpf, password='senha-antiga-123', is_superuser=superuser, is_staff=superuser or staff,
        )
        return Usuario.objects.create(user=user, funcionario=funcionario, papel=papel, acesso_rh=acesso_rh)

    def _confere(self, quem, alvos_permitidos, alvos_negados):
        for alvo in alvos_permitidos:
            with self.subTest(quem=quem.papel, alvo=alvo.user.username):
                self.assertTrue(quem.pode_redefinir_senha_de(alvo))
        for alvo in alvos_negados:
            with self.subTest(quem=quem.papel, alvo=alvo.user.username):
                self.assertFalse(quem.pode_redefinir_senha_de(alvo))

    # ----- a regra, linha a linha -----

    def test_colaborador_comum_e_redefinido_por_rh_dp_e_gestor_da_equipe(self):
        for quem in (self.rh, self.rh_por_acesso, self.dp, self.gestor):
            self._confere(quem, [self.colaborador], [])

    def test_conta_com_papel_so_o_rh_redefine(self):
        com_papel = [self.seguranca, self.dp_na_producao, self.acesso_rh_na_producao]
        self._confere(self.gestor, [], com_papel)
        self._confere(self.dp, [], com_papel)
        self._confere(self.rh, com_papel, [])
        self._confere(self.rh_por_acesso, com_papel, [])

    def test_rh_redefine_outro_rh_e_dp_nao(self):
        self._confere(self.rh, [self.outro_rh, self.rh_por_acesso], [])
        self._confere(self.dp, [], [self.outro_rh, self.rh_por_acesso])

    def test_ninguem_redefine_administrador_do_sistema_pelo_app(self):
        for quem in (self.rh, self.rh_por_acesso, self.dp, self.gestor):
            self._confere(quem, [], [self.superuser, self.staff])

    # ----- a view recusa mesmo com o formulário forçado -----

    def _redefinir(self, quem, alvo):
        self.client.force_login(quem.user)
        resposta = self.client.post(reverse('resetar_senha_funcionario', args=[alvo.funcionario_id]))
        alvo.user.refresh_from_db()
        return resposta

    def test_gestor_nao_redefine_seguranca_da_equipe(self):
        resposta = self._redefinir(self.gestor, self.seguranca)

        self.assertRedirects(resposta, reverse('detalhe_funcionario', args=[self.seguranca.funcionario_id]))
        self.assertTrue(self.seguranca.user.check_password('senha-antiga-123'))
        mensagens = [str(m) for m in get_messages(resposta.wsgi_request)]
        self.assertEqual(mensagens, ['Conta com papel no sistema: a senha só é redefinida pelo RH.'])

    def test_rh_nao_redefine_superuser(self):
        resposta = self._redefinir(self.rh_por_acesso, self.superuser)

        self.assertRedirects(resposta, reverse('detalhe_funcionario', args=[self.superuser.funcionario_id]))
        self.assertTrue(self.superuser.user.check_password('senha-antiga-123'))
        mensagens = [str(m) for m in get_messages(resposta.wsgi_request)]
        self.assertIn('pela TI', mensagens[0])

    def test_gestor_continua_redefinindo_colaborador_da_equipe(self):
        resposta = self._redefinir(self.gestor, self.colaborador)

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(self.colaborador.user.check_password(resposta.context['senha_gerada']))

    def test_rh_redefine_conta_com_papel(self):
        resposta = self._redefinir(self.rh, self.seguranca)

        self.assertEqual(resposta.status_code, 200)
        self.assertTrue(self.seguranca.user.check_password(resposta.context['senha_gerada']))

    # ----- as telas só oferecem o botão a quem pode -----

    def test_ficha_troca_o_botao_pelo_aviso(self):
        acao = reverse('resetar_senha_funcionario', args=[self.seguranca.funcionario_id])
        self.client.force_login(self.gestor.user)

        resposta = self.client.get(reverse('detalhe_funcionario', args=[self.seguranca.funcionario_id]))

        self.assertNotContains(resposta, acao)
        self.assertContains(resposta, 'a senha só é redefinida pelo RH')

        self.client.force_login(self.rh.user)
        self.assertContains(self.client.get(reverse('detalhe_funcionario', args=[self.seguranca.funcionario_id])), acao)

    def test_fila_de_pedidos_do_dp_so_oferece_o_que_ele_pode_atender(self):
        SolicitacaoSenha.objects.create(usuario=self.colaborador)
        SolicitacaoSenha.objects.create(usuario=self.outro_rh)
        SolicitacaoSenha.objects.create(usuario=self.superuser)
        self.client.force_login(self.dp.user)

        resposta = self.client.get(reverse('pedidos_senha'))

        self.assertContains(resposta, reverse('resetar_senha_funcionario', args=[self.colaborador.funcionario_id]))
        self.assertNotContains(resposta, reverse('resetar_senha_funcionario', args=[self.outro_rh.funcionario_id]))
        self.assertNotContains(resposta, reverse('resetar_senha_funcionario', args=[self.superuser.funcionario_id]))
        self.assertContains(resposta, 'a senha só é redefinida pelo RH')
        self.assertContains(resposta, 'a senha só é redefinida pela TI')
