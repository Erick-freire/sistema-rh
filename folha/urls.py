from django.urls import path

from . import views

app_name = 'folha'

urlpatterns = [
    path('', views.listar_fechamentos_view, name='listar'),
    path('abrir/', views.abrir_fechamento_view, name='abrir'),
    path('<int:fechamento_id>/', views.detalhe_fechamento_view, name='detalhe'),
    path('<int:fechamento_id>/recalcular/', views.recalcular_fechamento_view, name='recalcular'),
    path('<int:fechamento_id>/calcular-lote/', views.calcular_lote_view, name='calcular_lote'),
    path('<int:fechamento_id>/incluir/', views.incluir_funcionario_view, name='incluir_funcionario'),
    path('<int:fechamento_id>/fechar/', views.fechar_fechamento_view, name='fechar'),
    path('<int:fechamento_id>/reabrir/', views.reabrir_fechamento_view, name='reabrir'),
    path('<int:fechamento_id>/excluir/', views.excluir_fechamento_view, name='excluir'),
    path('<int:fechamento_id>/planilha.xlsx', views.exportar_xlsx_view, name='exportar_xlsx'),
    path('<int:fechamento_id>/fechamento.pdf', views.exportar_pdf_view, name='exportar_pdf'),

    path('premios/', views.premios_view, name='premios'),
    path('premios/<int:regra_id>/editar/', views.editar_regra_view, name='editar_regra'),
    path('premios/<int:regra_id>/excluir/', views.excluir_regra_view, name='excluir_regra'),
    path('premios/sugerir/', views.sugerir_regras_view, name='sugerir_regras'),
    path('premios/fixar/', views.fixar_regra_view, name='fixar_regra'),
    path('premios/programa/nova/', views.criar_regra_programa_view, name='criar_regra_programa'),
    path('premios/programa/<int:regra_id>/', views.regra_programa_view, name='regra_programa'),
    path('premios/programa/<int:regra_id>/incluir/', views.incluir_no_programa_view, name='incluir_no_programa'),
    path(
        'premios/programa/<int:regra_id>/remover/<int:funcionario_id>/',
        views.remover_do_programa_view, name='remover_do_programa',
    ),
    path('premios/programa/<int:regra_id>/excluir/', views.excluir_regra_programa_view, name='excluir_regra_programa'),
    path('premios/safra/nova/', views.criar_regra_safra_view, name='criar_regra_safra'),
    path('premios/safra/<int:regra_id>/', views.regra_safra_view, name='regra_safra'),
    path('premios/safra/<int:regra_id>/incluir/', views.incluir_na_safra_view, name='incluir_na_safra'),
    path(
        'premios/safra/<int:regra_id>/remover/<int:funcionario_id>/',
        views.remover_da_safra_view, name='remover_da_safra',
    ),
    path('premios/safra/<int:regra_id>/excluir/', views.excluir_regra_safra_view, name='excluir_regra_safra'),

    path('linhas/<int:linha_id>/salvar/', views.salvar_celula_view, name='salvar_celula'),
    path('linhas/<int:linha_id>/recalcular/', views.recalcular_linha_view, name='recalcular_linha'),
    path('linhas/<int:linha_id>/remover/', views.remover_linha_view, name='remover_linha'),
]
