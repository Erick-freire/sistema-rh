"""
Ação que muda dado só aceita POST.

Um GET pode ser disparado de fora sem a pessoa perceber: um link no WhatsApp,
uma imagem num e-mail. Com a sessão do RH aberta no navegador, abrir
"/funcionarios/12/alternar-acesso-rh/" daria acesso de RH a quem mandou o
link. O POST exige o token CSRF, que só as telas do próprio sistema têm.
"""
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from terceiros.tests import _criar_empresa, _criar_responsavel

from .models import Funcionario, Setor, Usuario

# Ações que o RH alcança. O id não precisa existir: o método é recusado antes
# de buscar o registro.
ACOES_DO_RH = [
    'gerar_acesso_funcionario', 'resetar_senha_funcionario', 'alternar_acesso_rh', 'gerar_contrato',
    'rejeitar_documento', 'validar_funcionario', 'aceitar_sugestao_ferias', 'adiar_ferias',
    'rh_concluir_ferias', 'marcar_aviso_visualizado', 'gerar_pdf_comodato', 'gerar_pdf_ficha_epi',
    'ponto:enviar_equipamento', 'ponto:receber_equipamento', 'ponto:excluir_horario', 'ponto:excluir_feriado',
    'ponto:aprovar_ajuste', 'ponto:rejeitar_ajuste',
    'terceiros:resetar_senha_responsavel', 'terceiros:alternar_status_empresa', 'terceiros:alternar_clausula',
    'terceiros:validar_empresa',
]
ACOES_DO_RH_SEM_ID = ['ponto:enviar_todos', 'ponto:receber_todos']
# Configurar relógio é só da TI (conta de administrador): o RH nem chega ao método.
ACOES_DA_TI = [
    'ponto:alternar_status_equipamento', 'ponto:excluir_equipamento', 'ponto:testar_conexao_equipamento',
]


def _usuario(cpf, papel, setor):
    funcionario = Funcionario.objects.create(nome=f'Pessoa {cpf}', cpf=cpf, cargo='Cargo', setor=setor, status='ativo')
    user = User.objects.create_user(username=cpf, password='senha-forte-123')
    Usuario.objects.create(user=user, funcionario=funcionario, papel=papel)
    return user, funcionario


class AcoesSoPorPostTests(TestCase):
    def setUp(self):
        self.setor = Setor.objects.create(nome='RH')
        self.user_rh, _ = _usuario('11111111111', 'rh', self.setor)

    def test_acoes_recusam_get(self):
        self.client.force_login(self.user_rh)
        urls = [reverse(nome, args=[999]) for nome in ACOES_DO_RH]
        urls += [reverse(nome) for nome in ACOES_DO_RH_SEM_ID]
        self.assertEqual(len(urls), 24)

        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)

    def test_acoes_da_ti_recusam_get(self):
        user_ti, _ = _usuario('44444444444', 'colaborador', self.setor)
        User.objects.filter(pk=user_ti.pk).update(is_staff=True)
        self.client.force_login(user_ti)

        for nome in ACOES_DA_TI:
            with self.subTest(acao=nome):
                self.assertEqual(self.client.get(reverse(nome, args=[999])).status_code, 405)

    def test_gestor_nao_aprova_ferias_por_get(self):
        user_gestor, _ = _usuario('22222222222', 'gestor', self.setor)
        self.client.force_login(user_gestor)

        resposta = self.client.get(reverse('gestor_aprovar_ferias', args=[999]))

        self.assertEqual(resposta.status_code, 405)

    def test_prestadora_nao_envia_para_validacao_por_get(self):
        self.client.force_login(_criar_responsavel(_criar_empresa()).user)

        resposta = self.client.get(reverse('terceiros:enviar_para_validacao'))

        self.assertEqual(resposta.status_code, 405)

    def test_link_de_alternar_acesso_rh_nao_concede_acesso(self):
        _, colaborador = _usuario('33333333333', 'colaborador', self.setor)
        self.client.force_login(self.user_rh)

        self.client.get(reverse('alternar_acesso_rh', args=[colaborador.id]))

        self.assertFalse(Usuario.objects.get(funcionario=colaborador).acesso_rh)

    def test_post_de_fora_sem_token_csrf_e_recusado(self):
        """O que sobra depois de recusar o GET: um formulário em outro site não tem o token."""
        _, colaborador = _usuario('33333333333', 'colaborador', self.setor)
        navegador = Client(enforce_csrf_checks=True)
        navegador.force_login(self.user_rh)

        resposta = navegador.post(reverse('alternar_acesso_rh', args=[colaborador.id]))

        self.assertEqual(resposta.status_code, 403)
        self.assertFalse(Usuario.objects.get(funcionario=colaborador).acesso_rh)

    def test_link_de_sair_nao_derruba_a_sessao(self):
        self.client.force_login(self.user_rh)

        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_botao_sair_e_um_formulario_post(self):
        self.client.force_login(self.user_rh)

        resposta = self.client.get(reverse('dashboard'))

        self.assertContains(resposta, f'<form method="post" action="{reverse("logout")}"')
        self.assertNotContains(resposta, f'href="{reverse("logout")}"')
