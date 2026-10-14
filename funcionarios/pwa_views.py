import json
import os

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .models import InscricaoPush
from .notificacoes import endpoint_permitido

# Guarda na sessão o endpoint do aparelho, para o logout saber qual inscrição
# desvincular (ver `desvincular_push_da_sessao`).
CHAVE_SESSAO_PUSH = 'push_endpoint'


@never_cache
def service_worker_view(request):
    caminho = os.path.join(settings.BASE_DIR, 'static', 'pwa', 'service-worker.js')
    with open(caminho, 'rb') as arquivo:
        resposta = HttpResponse(arquivo.read(), content_type='application/javascript; charset=utf-8')
    resposta['Service-Worker-Allowed'] = '/'
    return resposta


def offline_view(request):
    return render(request, 'funcionarios/offline.html')


def _ler_json(request):
    try:
        return json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return None


@login_required
@require_POST
def inscrever_push_view(request):
    dados = _ler_json(request)
    if not isinstance(dados, dict):
        return HttpResponseBadRequest('JSON inválido.')
    endpoint = dados.get('endpoint')
    chaves = dados.get('keys') if isinstance(dados.get('keys'), dict) else {}
    p256dh, auth = chaves.get('p256dh'), chaves.get('auth')

    if not (isinstance(endpoint, str) and len(endpoint) <= 500 and endpoint_permitido(endpoint)):
        return HttpResponseBadRequest('Endpoint de push não reconhecido.')
    if not (isinstance(p256dh, str) and isinstance(auth, str) and 0 < len(p256dh) <= 200 and 0 < len(auth) <= 100):
        return HttpResponseBadRequest('Chaves da inscrição inválidas.')

    # Por endpoint, não por usuário: no aparelho compartilhado, quem entrou
    # por último passa a ser o dono da inscrição.
    InscricaoPush.objects.update_or_create(
        endpoint=endpoint,
        defaults={
            'user': request.user,
            'p256dh': p256dh,
            'auth': auth,
            'navegador': request.META.get('HTTP_USER_AGENT', '')[:300],
        },
    )
    request.session[CHAVE_SESSAO_PUSH] = endpoint
    return HttpResponse(status=204)


@login_required
@require_POST
def cancelar_push_view(request):
    dados = _ler_json(request)
    endpoint = dados.get('endpoint') if isinstance(dados, dict) else None
    if isinstance(endpoint, str):
        InscricaoPush.objects.filter(user=request.user, endpoint=endpoint).delete()
    request.session.pop(CHAVE_SESSAO_PUSH, None)
    return HttpResponse(status=204)


def desvincular_push_da_sessao(request):
    """Chamado no logout: o aparelho para de receber as notificações de quem saiu.

    A permissão do navegador continua; quando alguém entrar de novo nesse
    aparelho, a inscrição é refeita em nome dessa pessoa.
    """
    endpoint = request.session.get(CHAVE_SESSAO_PUSH)
    if endpoint and request.user.is_authenticated:
        InscricaoPush.objects.filter(user=request.user, endpoint=endpoint).delete()
