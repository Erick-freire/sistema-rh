from django.contrib import admin
from django.urls import include, path

# Sem rota para /media/, de propósito: o caminho direto pulava a checagem de
# quem pode ver o arquivo -- bastava acertar o nome (ex.: ficha_epi_<id>_<cpf>.pdf)
# -- e o Cloudflare ainda guardava .jpg/.pdf por 2 horas para quem pedisse,
# sem login. Todo arquivo enviado sai por uma view que confere a permissão e
# o tipo pelo conteúdo (`funcionarios.arquivos.resposta_de_arquivo`):
# documentos, comodatos, fichas e ocorrências de EPI, contratos e documentos
# de terceiros e a foto de perfil. Arquivo de um tipo novo ganha a sua view.
urlpatterns = [
    path('admin/', admin.site.urls),
    path('ponto/', include('ponto.urls')),
    path('terceiros/', include('terceiros.urls')),
    path('folha/', include('folha.urls')),
    path('', include('funcionarios.urls')),
]
