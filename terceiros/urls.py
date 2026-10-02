from django.urls import path

from . import views

app_name = 'terceiros'

urlpatterns = [
    # RH
    path('', views.listar_empresas_view, name='listar_empresas'),
    path('nova/', views.cadastrar_empresa_view, name='cadastrar_empresa'),
    path('clausulas/', views.listar_clausulas_view, name='listar_clausulas'),
    path('clausulas/<int:clausula_id>/editar/', views.editar_clausula_view, name='editar_clausula'),
    path('clausulas/<int:clausula_id>/alternar/', views.alternar_clausula_view, name='alternar_clausula'),
    path('<int:empresa_id>/', views.detalhe_empresa_view, name='detalhe_empresa'),
    path('<int:empresa_id>/contrato/', views.gerar_contrato_view, name='gerar_contrato'),
    path('<int:empresa_id>/resetar-senha/', views.resetar_senha_responsavel_view, name='resetar_senha_responsavel'),
    path('<int:empresa_id>/validar/', views.validar_empresa_view, name='validar_empresa'),
    path('<int:empresa_id>/alternar-status/', views.alternar_status_empresa_view, name='alternar_status_empresa'),
    path('documentos/<int:documento_id>/aprovar/', views.aprovar_documento_view, name='aprovar_documento'),
    path('documentos/<int:documento_id>/rejeitar/', views.rejeitar_documento_view, name='rejeitar_documento'),
    path('documentos-empresa/<int:documento_id>/aprovar/', views.aprovar_documento_empresa_view, name='aprovar_documento_empresa'),
    path('documentos-empresa/<int:documento_id>/rejeitar/', views.rejeitar_documento_empresa_view, name='rejeitar_documento_empresa'),

    # Portal do Prestador -- as três áreas da sidebar, mais o Início
    path('portal/', views.painel_view, name='painel'),
    path('portal/minha-empresa/', views.minha_empresa_view, name='minha_empresa'),
    path('portal/documentacao-mensal/', views.documentacao_mensal_view, name='documentacao_mensal'),
    path('portal/funcionarios/', views.funcionarios_view, name='funcionarios'),
    path('portal/funcionarios/nova/', views.cadastrar_colaborador_view, name='cadastrar_colaborador'),
    path('portal/funcionarios/<int:colaborador_id>/', views.detalhe_colaborador_view, name='detalhe_colaborador'),
    path('portal/funcionarios/<int:colaborador_id>/editar/', views.editar_colaborador_view, name='editar_colaborador'),
    path('portal/funcionarios/<int:colaborador_id>/remover/', views.remover_colaborador_view, name='remover_colaborador'),
    path('portal/enviar-para-validacao/', views.enviar_para_validacao_view, name='enviar_para_validacao'),

    # Arquivos -- permissão verificada dentro da view, nunca por link direto ao media
    path('contratos/<int:contrato_id>/arquivo/', views.baixar_contrato_view, name='baixar_contrato'),
    path('documentos/<int:documento_id>/arquivo/', views.ver_documento_view, name='ver_documento'),
    path('documentos-empresa/<int:documento_id>/arquivo/', views.ver_documento_empresa_view, name='ver_documento_empresa'),
]
