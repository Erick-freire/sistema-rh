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
    path('funcionarios/<int:funcionario_id>/gerar-acesso/', views.gerar_acesso_funcionario_view, name='gerar_acesso_funcionario'),
    path('funcionarios/novo/', views.cadastrar_funcionario_view, name='cadastrar_funcionario'),
    path('funcionarios/<int:funcionario_id>/', views.detalhe_funcionario_view, name='detalhe_funcionario'),
    path('trocar-senha/', views.trocar_senha_view, name='trocar_senha'),
    path('ferias/', views.solicitar_ferias_view, name='solicitar_ferias'),
    path('ferias/<int:solicitacao_id>/aceitar-sugestao/', views.aceitar_sugestao_ferias_view, name='aceitar_sugestao_ferias'),
    path('ferias/<int:solicitacao_id>/propor-outra-data/', views.propor_outra_data_ferias_view, name='propor_outra_data_ferias'),
    path('ferias/<int:solicitacao_id>/adiar/', views.adiar_ferias_view, name='adiar_ferias'),

    path('gestor/ferias/', views.gestor_listar_ferias_view, name='gestor_listar_ferias'),
    path('gestor/ferias/<int:solicitacao_id>/aprovar/', views.gestor_aprovar_ferias_view, name='gestor_aprovar_ferias'),
    path('gestor/ferias/<int:solicitacao_id>/sugerir/', views.gestor_sugerir_ferias_view, name='gestor_sugerir_ferias'),

    path('rh/ferias/', views.rh_listar_ferias_view, name='rh_listar_ferias'),
    path('rh/ferias/<int:solicitacao_id>/concluir/', views.rh_concluir_ferias_view, name='rh_concluir_ferias'),
]