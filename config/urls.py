import posixpath

from django.conf import settings
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.urls import include, path, re_path
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.static import serve

# Arquivos que têm view própria, com verificação de dono/RH/gestor
# (ver_documento_view e ver_comodato_view). Pelo caminho direto em /media/
# eles não podem ser abertos, senão qualquer usuário logado pularia essa
# verificação digitando o endereço do arquivo.
PREFIXOS_COM_VIEW_PROPRIA = ('documentos/', 'comodatos/')


def _normalizar(caminho):
    """Resolve '..', '.', barras repetidas e barras invertidas, do mesmo jeito
    que o serve() faz antes de abrir o arquivo. Sem isso, um endereço como
    'fotos_perfil/../documentos/rg.pdf' passaria pela checagem de prefixo e
    abriria o documento mesmo assim."""
    caminho = caminho.replace('\\', '/')
    return posixpath.normpath(caminho).lstrip('/')


@login_required
@xframe_options_sameorigin  # permite exibir o arquivo num popup (iframe) dentro do próprio sistema
def servir_midia(request, caminho):
    """Serve os arquivos de mídia sem view própria (fotos, fichas de EPI,
    terceiros) só para usuários logados.

    Solução para o uso interno. Antes de abrir o sistema para fora, os
    arquivos sensíveis daqui (fichas de EPI, documentos de terceiros) devem
    ganhar views próprias com checagem de permissão, como os documentos e
    os comodatos.
    """
    caminho = _normalizar(caminho)
    # .lower(): o Windows não diferencia maiúsculas, então "Documentos/" e
    # "documentos/" apontam para a mesma pasta.
    if caminho.lower().startswith(PREFIXOS_COM_VIEW_PROPRIA):
        raise Http404
    return serve(request, caminho, document_root=settings.MEDIA_ROOT)


urlpatterns = [
    path('admin/', admin.site.urls),
    path('ponto/', include('ponto.urls')),
    path('terceiros/', include('terceiros.urls')),
    path('folha/', include('folha.urls')),
    re_path(rf'^{settings.MEDIA_URL.lstrip("/")}(?P<caminho>.*)$', servir_midia),
    path('', include('funcionarios.urls')),
]