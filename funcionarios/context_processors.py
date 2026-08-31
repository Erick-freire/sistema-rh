from ponto.models import AjustePonto

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

    if 'badge_ferias_gestor' in contexto or 'badge_ferias_rh' in contexto:
        contexto['badge_ferias_total'] = contexto.get('badge_ferias_gestor', 0) + contexto.get('badge_ferias_rh', 0)

    if usuario.papel == 'rh' or usuario.acesso_rh:
        contexto['badge_ajustes_pendentes'] = AjustePonto.objects.filter(status='pendente').count()

    if usuario.papel == 'colaborador':
        funcionario = usuario.funcionario
        contexto['documentos_pendentes'] = (
            funcionario.documentos.filter(status='rejeitado').exists()
            or funcionario.status in ('pendente_documentos', 'aguardando_assinatura')
        )
        contexto['badge_documentos'] = len(funcionario.tipos_documentos_nunca_enviados())
        contexto['aviso_ferias_pendente'] = funcionario.aviso_ferias_pendente_acao() is not None

    return contexto
