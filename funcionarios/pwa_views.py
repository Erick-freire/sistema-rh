import os

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache


@never_cache
def service_worker_view(request):
    caminho = os.path.join(settings.BASE_DIR, 'static', 'pwa', 'service-worker.js')
    with open(caminho, 'rb') as arquivo:
        resposta = HttpResponse(arquivo.read(), content_type='application/javascript; charset=utf-8')
    resposta['Service-Worker-Allowed'] = '/'
    return resposta


def offline_view(request):
    return render(request, 'funcionarios/offline.html')
