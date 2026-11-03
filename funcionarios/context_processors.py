from django.conf import settings

from ponto.models import AjustePonto
from terceiros.models import EmpresaTerceira

from .models import AvisoFerias, FichaEPI, SolicitacaoFerias, SolicitacaoSenha, TermoComodato, Usuario
from .perfis import responsavel_terceiro_de
from .pwa_views import CHAVE_SESSAO_PUSH

# Rotas que a sidebar do Portal do Prestador acende sob "Funcionários" -- a
# lista da equipe e tudo que se abre a partir dela. Fica aqui, e não no
# template, porque o template do Django não monta lista literal.
URLS_DA_EQUIPE_TERCEIRO = (
    'funcionarios', 'detalhe_colaborador', 'cadastrar_colaborador', 'editar_colaborador',
)


def usuario_logado(request):
    if not request.user.is_authenticated:
        return {'usuario_atual': None, 'responsavel_atual': None}

    try:
        usuario = request.user.usuario
    except Usuario.DoesNotExist:
        # Pode ser uma conta de responsável de empresa terceira -- tipo de conta
        # paralelo, sem `Usuario` (ver funcionarios/perfis.py). `responsavel_atual`
        # é o equivalente de `usuario_atual` para o Portal do Prestador, e é o que
        # o `terceiros/base_terceiro.html` usa para se identificar.
        return {
            'usuario_atual': None,
            'responsavel_atual': responsavel_terceiro_de(request.user),
            'urls_da_equipe': URLS_DA_EQUIPE_TERCEIRO,
        }

    contexto = {'usuario_atual': usuario, 'responsavel_atual': None}

    # Notificações push: só para quem tem `Usuario` (o Portal do Prestador não
    # recebe) e só com as chaves VAPID configuradas.
    if settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY:
        contexto['push_chave_publica'] = settings.VAPID_PUBLIC_KEY
        contexto['push_inscrito_na_sessao'] = bool(request.session.get(CHAVE_SESSAO_PUSH))

    if usuario.papel == 'gestor':
        contexto['badge_ferias_gestor'] = SolicitacaoFerias.objects.filter(
            status='pendente_gestor',
            funcionario__setor=usuario.funcionario.setor,
        ).count()

    if usuario.papel == 'gestor' or usuario.eh_dp_ou_rh():
        badge_concluir_ferias = 0
        if usuario.eh_dp_ou_rh():
            badge_concluir_ferias += SolicitacaoFerias.objects.filter(status='pendente_rh').count()

        avisos_pendentes = AvisoFerias.objects.filter(visualizado=False)
        if usuario.papel == 'gestor':
            avisos_pendentes = avisos_pendentes.filter(funcionario__setor__gestor=usuario.funcionario)
        badge_concluir_ferias += avisos_pendentes.count()

        contexto['badge_ferias_rh'] = badge_concluir_ferias

    if 'badge_ferias_gestor' in contexto or 'badge_ferias_rh' in contexto:
        contexto['badge_ferias_total'] = contexto.get('badge_ferias_gestor', 0) + contexto.get('badge_ferias_rh', 0)

    if usuario.eh_dp_ou_rh():
        contexto['badge_ajustes_pendentes'] = AjustePonto.objects.filter(status='pendente').count()
        # Empresas terceiras esperando uma ação do RH: gerar contrato ou validar
        # a documentação enviada pela prestadora.
        contexto['badge_terceiros'] = EmpresaTerceira.objects.filter(
            status__in=('aguardando_contrato', 'em_validacao')
        ).count()
        # Termos de comodato que o colaborador já devolveu assinados.
        contexto['badge_comodatos'] = TermoComodato.objects.filter(status='em_validacao').count()
        # Pedidos de nova senha feitos pela tela de login.
        contexto['badge_pedidos_senha'] = SolicitacaoSenha.pendentes().count()
    elif usuario.papel == 'gestor':
        # O gestor também analisa os pedidos de ajuste de ponto, mas só os da equipe.
        contexto['badge_ajustes_pendentes'] = AjustePonto.objects.filter(
            status='pendente', funcionario__in=usuario.equipe(),
        ).count()

    if usuario.eh_seguranca_ou_rh():
        # Fichas de EPI que o colaborador já devolveu assinadas.
        contexto['badge_fichas_epi'] = FichaEPI.objects.filter(status='em_validacao').count()
        # Colaboradores desligados que ainda estão com EPI.
        contexto['badge_devolucoes_epi'] = FichaEPI.com_devolucao_pendente().count()

    if usuario.papel == 'colaborador':
        funcionario = usuario.funcionario
        contexto['documentos_pendentes'] = (
            funcionario.documentos.filter(status='rejeitado').exists()
            or funcionario.status in ('pendente_documentos', 'aguardando_assinatura')
        )
        contexto['badge_documentos'] = len(funcionario.tipos_documentos_nunca_enviados())
        contexto['aviso_ferias_pendente'] = funcionario.aviso_ferias_pendente_acao() is not None

    return contexto
