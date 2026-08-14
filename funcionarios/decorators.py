from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages
from .models import Usuario


def papel_required(*papeis_permitidos):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            try:
                usuario = request.user.usuario
            except Usuario.DoesNotExist:
                return redirect('dashboard')

            tem_permissao = usuario.papel in papeis_permitidos or (
                'rh' in papeis_permitidos and usuario.acesso_rh
            )
            if not tem_permissao:
                messages.error(request, 'Você não tem permissão para acessar essa página.')
                return redirect('dashboard')

            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator