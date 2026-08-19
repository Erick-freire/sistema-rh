from .models import AvisoFerias, SolicitacaoFerias, Usuario


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

    if usuario.papel in ('gestor', 'rh') or usuario.acesso_rh:
        badge_concluir_ferias = 0
        if usuario.papel == 'rh' or usuario.acesso_rh:
            badge_concluir_ferias += SolicitacaoFerias.objects.filter(status='pendente_rh').count()

        avisos_pendentes = AvisoFerias.objects.filter(visualizado=False)
        if usuario.papel == 'gestor':
            avisos_pendentes = avisos_pendentes.filter(funcionario__setor__gestor=usuario.funcionario)
        badge_concluir_ferias += avisos_pendentes.count()

        contexto['badge_ferias_rh'] = badge_concluir_ferias

    if usuario.papel == 'colaborador':
        contexto['documentos_pendentes'] = not usuario.funcionario.cadastro_completo()
        contexto['aviso_ferias_pendente'] = usuario.funcionario.aviso_ferias_pendente_acao() is not None

    return contexto
