from django.shortcuts import redirect
from django.urls import reverse

from .models import Usuario


class SenhaProvisoriaMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            try:
                usuario = request.user.usuario
                rotas_permitidas = [reverse('trocar_senha'), reverse('logout')]

                if usuario.senha_provisoria and request.path not in rotas_permitidas:
                    return redirect('trocar_senha')
            except Usuario.DoesNotExist:
                pass

        return self.get_response(request)