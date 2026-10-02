from functools import wraps

from django.contrib import messages
from django.shortcuts import redirect

from funcionarios.perfis import responsavel_terceiro_de


def responsavel_required(view_func):
    """
    Restringe a view ao responsável de uma empresa terceira.

    Não usa o sistema de papéis de `funcionarios` de propósito: é um tipo de
    conta paralelo (ver `terceiros.models.ResponsavelTerceiro`). O responsável
    resolvido fica em `request.responsavel`, e toda consulta das views dele sai
    de `request.responsavel.empresa` -- é isso que garante que um responsável
    nunca alcance dados de outra prestadora.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        responsavel = responsavel_terceiro_de(request.user)
        if responsavel is None:
            messages.error(request, 'Você não tem permissão para acessar essa página.')
            return redirect('dashboard')

        request.responsavel = responsavel
        return view_func(request, *args, **kwargs)

    return wrapper
