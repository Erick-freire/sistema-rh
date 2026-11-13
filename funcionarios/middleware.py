import time

from django.conf import settings
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse

from .perfis import perfil_de_acesso

CHAVE_INICIO_DO_LOGIN = 'login_em'


def marcar_inicio_do_login(sender, request, user, **kwargs):
    """Receptor do `user_logged_in` (ligado em `FuncionariosConfig.ready`)."""
    if request is not None and hasattr(request, 'session'):
        request.session[CHAVE_INICIO_DO_LOGIN] = time.time()


class LimiteDoLoginMiddleware:
    """
    O login vale no máximo `DURACAO_MAXIMA_DO_LOGIN` (12 horas), mesmo para quem
    usa sem parar: um cookie de sessão roubado deixa de valer no mesmo dia. O
    limite de 1 hora sem uso é do próprio Django (`SESSION_COOKIE_AGE` com
    `SESSION_SAVE_EVERY_REQUEST`, em config/settings.py).

    Sessão aberta antes desta regra (sem a marca do login) começa a contar na
    primeira requisição depois do deploy.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            agora = time.time()
            inicio = request.session.get(CHAVE_INICIO_DO_LOGIN)
            if inicio is None:
                request.session[CHAVE_INICIO_DO_LOGIN] = agora
            elif agora - inicio > settings.DURACAO_MAXIMA_DO_LOGIN:
                logout(request)  # segue como anônimo: o login_required manda para a tela de login
        return self.get_response(request)


class SoCacheDoNavegadorMiddleware:
    """
    `Cache-Control: private` em toda resposta do sistema que não disser outra
    coisa: só o navegador de quem pediu guarda, nunca um cache no meio do
    caminho. O Cloudflare guarda sozinho, por 2 horas, endereço terminado em
    .pdf/.xlsx/.jpg sem Cache-Control -- e ignora o `Vary: Cookie`, então
    entregaria a exportação da folha (`/folha/<id>/planilha.xlsx`) a quem
    pedisse, sem login. Também guardaria o redirect para o login (20 min).

    Fica logo depois do WhiteNoise: os estáticos (/static/) saem antes de
    chegar aqui e continuam com o cache público dele.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        resposta = self.get_response(request)
        if 'Cache-Control' not in resposta:
            resposta['Cache-Control'] = 'private'
        return resposta


class SenhaProvisoriaMiddleware:
    """
    Obriga a troca da senha no primeiro acesso.

    Vale para os dois tipos de conta -- colaborador CLT (`Usuario`) e
    responsável de empresa terceira (`ResponsavelTerceiro`) -- porque ambos têm
    o campo `senha_provisoria` e recebem a senha inicial pelas mãos do RH.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            _, perfil = perfil_de_acesso(request.user)
            rotas_permitidas = [reverse('trocar_senha'), reverse('logout')]

            if perfil is not None and perfil.senha_provisoria and request.path not in rotas_permitidas:
                return redirect('trocar_senha')

        return self.get_response(request)
