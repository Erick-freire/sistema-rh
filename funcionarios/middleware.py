from django.shortcuts import redirect
from django.urls import reverse

from .perfis import perfil_de_acesso


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
