from .models import SolicitacaoFerias, Usuario


def usuario_logado(request):
    if not request.user.is_authenticated:
        return {'usuario_atual': None}

    try:
        usuario = request.user.usuario
    except Usuario.DoesNotExist:
        return {'usuario_atual': None}

    contexto = {'usuario_atual': usuario}

    if usuario.papel == 'gestor':
        contexto['badge_ferias_gestor'] = SolicitacaoFerias.objects.filter(
            status='pendente_gestor',
            funcionario__setor=usuario.funcionario.setor,
        ).count()
    if usuario.papel == 'rh' or usuario.acesso_rh:
        contexto['badge_ferias_rh'] = SolicitacaoFerias.objects.filter(
            status='pendente_rh'
        ).count()

    if usuario.papel == 'colaborador':
        contexto['documentos_pendentes'] = not usuario.funcionario.cadastro_completo()

    return contexto
