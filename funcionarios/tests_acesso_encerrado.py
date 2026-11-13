"""
Quem saiu não entra mais (`perfis.acesso_encerrado`): CLT desligado e
responsável de prestadora inativada. Vale para o login, para a sessão que já
estava aberta e para os avisos no celular. Reativou, volta a entrar.
"""
from unittest import mock

from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from terceiros.tests import _criar_empresa, _criar_responsavel

from .models import Funcionario, InscricaoPush, Setor, Usuario
from .notificacoes import notificar
from .tests_notificacoes import ENDPOINT_GOOGLE, PUSH_LIGADO
from .views import MENSAGEM_CREDENCIAL_INVALIDA

SENHA = 'senha-forte-123'


class AcessoEncerradoTests(TestCase):
    def setUp(self):
        self.setor = Setor.objects.create(nome='Produção')
        self.funcionario = Funcionario.objects.create(
            nome='Ana', cpf='11111111111', cargo='Operadora', setor=self.setor, status='ativo',
        )
        self.user = User.objects.create_user(username='11111111111', password=SENHA)
        Usuario.objects.create(user=self.user, funcionario=self.funcionario, papel='gestor')

        self.empresa = _criar_empresa(status='ativo')
        self.responsavel = _criar_responsavel(self.empresa)

    def _status(self, status):
        self.funcionario.status = status
        self.funcionario.save()

    def _status_empresa(self, status):
        self.empresa.status = status
        self.empresa.save()

    # ----- CLT desligado -----

    def test_desligado_nao_entra_e_ve_a_mensagem_de_senha_errada(self):
        self._status('inativo')

        resposta = self.client.post(reverse('login'), {'username': '111.111.111-11', 'password': SENHA})

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.context['erro'], MENSAGEM_CREDENCIAL_INVALIDA)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_desligado_cai_da_sessao_que_ja_estava_aberta(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

        self._status('inativo')
        resposta = self.client.get(reverse('dashboard'))

        self.assertRedirects(resposta, f"{reverse('login')}?next=/", fetch_redirect_response=False)

    def test_reativado_volta_a_entrar(self):
        self._status('inativo')
        self.assertIsNone(authenticate(username='11111111111', password=SENHA))

        self._status('ativo')

        self.assertEqual(authenticate(username='11111111111', password=SENHA), self.user)

    def test_quem_ainda_esta_na_admissao_entra(self):
        """Só 'inativo' bloqueia: o recém-contratado entra para mandar os documentos."""
        for status in ('pendente_documentos', 'aguardando_contrato', 'aguardando_assinatura', 'em_validacao'):
            with self.subTest(status=status):
                self._status(status)
                self.assertEqual(authenticate(username='11111111111', password=SENHA), self.user)

    # ----- prestadora inativada -----

    def test_responsavel_de_empresa_inativa_nao_entra(self):
        self._status_empresa('inativo')

        self.assertIsNone(authenticate(username=self.responsavel.cpf, password='senha-forte-123'))

    def test_responsavel_cai_da_sessao_quando_o_rh_inativa(self):
        self.client.force_login(self.responsavel.user)
        self.assertEqual(self.client.get(reverse('terceiros:painel')).status_code, 200)

        self._status_empresa('inativo')
        resposta = self.client.get(reverse('terceiros:painel'))

        self.assertEqual(resposta.status_code, 302)
        self.assertTrue(resposta['Location'].startswith(reverse('login')))

    def test_empresa_reativada_volta_a_entrar(self):
        self._status_empresa('inativo')
        self._status_empresa('ativo')

        self.assertEqual(authenticate(username=self.responsavel.cpf, password='senha-forte-123'), self.responsavel.user)

    def test_empresa_em_cadastro_entra(self):
        self._status_empresa('pendente_cadastro')

        self.assertEqual(authenticate(username=self.responsavel.cpf, password='senha-forte-123'), self.responsavel.user)

    # ----- outros -----

    def test_superuser_sem_perfil_continua_entrando(self):
        User.objects.create_superuser(username='admin', password=SENHA)

        self.assertIsNotNone(authenticate(username='admin', password=SENHA))

    @PUSH_LIGADO
    def test_desligado_nao_recebe_aviso_no_celular(self):
        InscricaoPush.objects.create(user=self.user, endpoint=f'{ENDPOINT_GOOGLE}-ana', p256dh='p', auth='a')
        self._status('inativo')

        with mock.patch('funcionarios.notificacoes.webpush') as webpush, \
                self.captureOnCommitCallbacks(execute=True):
            notificar([self.user], 'Pedido de férias', 'Fulano pediu férias', '/')

        webpush.assert_not_called()
