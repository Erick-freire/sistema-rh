from django.urls import path
from . import views

urlpatterns = [
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('', views.dashboard_view, name='dashboard'),
    path('meu-perfil/', views.meu_perfil_view, name='meu_perfil'),
    path('enviar-documento/', views.enviar_documento_view, name='enviar_documento'),
    path('meus-documentos/', views.meus_documentos_view, name='meus_documentos'),
    path('funcionarios/', views.listar_funcionarios_view, name='listar_funcionarios'),
    path('funcionarios/novo/', views.cadastrar_funcionario_view, name='cadastrar_funcionario'),
]