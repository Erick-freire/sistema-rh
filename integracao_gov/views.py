import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from funcionarios.models import Usuario

from . import client, services
from .forms import EnviarParaAssinaturaForm
from .models import SolicitacaoAssinatura, TokenGovBr

CHAVE_SESSION_STATE = 'govbr_oauth_state'
CHAVE_SESSION_SOLICITACAO = 'govbr_solicitacao_pendente'


# ---------- Colaborador ----------

@login_required
def minhas_assinaturas_view(request):
    try:
        funcionario = request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return render(request, 'funcionarios/sem_perfil.html')

    if request.method == 'POST':
        form = EnviarParaAssinaturaForm(request.POST, request.FILES)
        if form.is_valid():
            solicitacao = form.save(commit=False)
            solicitacao.funcionario = funcionario
            solicitacao.solicitado_por = request.user
            solicitacao.save()

            try:
                token = request.user.token_govbr
            except TokenGovBr.DoesNotExist:
                token = None

            if token is None or token.expirado():
                return redirect('iniciar_login_gov', solicitacao_id=solicitacao.id)

            if services.enviar_para_gov_br(solicitacao, token.access_token):
                messages.success(request, 'Documento enviado para assinatura no gov.br.')
            else:
                messages.error(request, 'Não foi possível enviar o documento para assinatura. Tente novamente mais tarde.')

            return redirect('minhas_assinaturas')
    else:
        form = EnviarParaAssinaturaForm()

    return render(request, 'integracao_gov/minhas_assinaturas.html', {
        'form': form,
        'solicitacoes': funcionario.solicitacoes_assinatura.all(),
    })


# ---------- Login gov.br (Conecta) ----------

@login_required
def iniciar_login_gov_view(request, solicitacao_id):
    get_object_or_404(SolicitacaoAssinatura, id=solicitacao_id, solicitado_por=request.user)

    state = client.gerar_state()
    request.session[CHAVE_SESSION_STATE] = state
    request.session[CHAVE_SESSION_SOLICITACAO] = solicitacao_id

    return redirect(client.montar_url_autorizacao(state))


@login_required
def callback_gov_view(request):
    state_recebido = request.GET.get('state')
    state_esperado = request.session.pop(CHAVE_SESSION_STATE, None)
    solicitacao_id = request.session.pop(CHAVE_SESSION_SOLICITACAO, None)

    if not state_recebido or state_recebido != state_esperado:
        messages.error(request, 'Não foi possível confirmar o login com o gov.br. Tente novamente.')
        return render(request, 'integracao_gov/erro_gov.html', status=400)

    code = request.GET.get('code')
    if not code:
        messages.error(request, 'Login com o gov.br cancelado ou não concluído.')
        return render(request, 'integracao_gov/erro_gov.html')

    try:
        dados_token = client.trocar_code_por_token(code)
    except client.GovBrAPIError:
        messages.error(request, 'Falha ao concluir o login com o gov.br. Tente novamente mais tarde.')
        return render(request, 'integracao_gov/erro_gov.html')

    expira_em = timezone.now() + timezone.timedelta(seconds=dados_token.get('expires_in', 300))

    token, _ = TokenGovBr.objects.update_or_create(
        usuario=request.user,
        defaults={
            'access_token': dados_token['access_token'],
            'refresh_token': dados_token.get('refresh_token', ''),
            'expira_em': expira_em,
        },
    )

    messages.success(request, 'Login com gov.br realizado com sucesso.')

    solicitacao = SolicitacaoAssinatura.objects.filter(id=solicitacao_id, solicitado_por=request.user).first()
    if solicitacao is not None:
        if services.enviar_para_gov_br(solicitacao, token.access_token):
            messages.success(request, 'Documento enviado para assinatura no gov.br.')
        else:
            messages.error(request, 'Não foi possível enviar o documento para assinatura. Tente novamente mais tarde.')

    return redirect('minhas_assinaturas')


# ---------- Verificação de status ----------

@csrf_exempt
def webhook_status_assinatura_view(request):
    """
    Endpoint chamado pelo gov.br quando o status de uma assinatura muda (uma das duas
    abordagens de verificação — ver especificação, seção 5.2). Formato exato do payload
    e do cabeçalho de assinatura/segredo ainda pendentes de confirmação (seção 8);
    abaixo assume-se JSON no corpo e um segredo compartilhado em header.
    """
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    segredo_recebido = request.headers.get('X-Govbr-Webhook-Secret', '')
    if not settings.GOVBR_WEBHOOK_SECRET or segredo_recebido != settings.GOVBR_WEBHOOK_SECRET:
        return HttpResponse(status=401)

    try:
        payload = json.loads(request.body)
    except ValueError:
        return HttpResponseBadRequest('Payload inválido.')

    protocolo_gov = payload.get('protocolo')
    novo_status = payload.get('status')

    solicitacao = SolicitacaoAssinatura.objects.filter(protocolo_gov=protocolo_gov).first()
    if solicitacao is None:
        return HttpResponse(status=404)

    services.atualizar_status_solicitacao(solicitacao, novo_status, payload.get('url_arquivo_assinado'))

    return HttpResponse(status=200)
