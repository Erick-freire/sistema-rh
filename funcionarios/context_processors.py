def usuario_logado(request):
    if request.user.is_authenticated:
        try:
            return {'usuario_atual': request.user.usuario}
        except request.user.usuario.__class__.DoesNotExist:
            return {'usuario_atual': None}
    return {'usuario_atual': None}