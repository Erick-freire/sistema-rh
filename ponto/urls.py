from django.urls import path

from . import views

app_name = 'ponto'

urlpatterns = [
    path('equipamentos/', views.listar_equipamentos_view, name='listar_equipamentos'),
    path('equipamentos/novo/', views.cadastrar_equipamento_view, name='cadastrar_equipamento'),
    path('equipamentos/<int:equipamento_id>/editar/', views.editar_equipamento_view, name='editar_equipamento'),
    path('equipamentos/<int:equipamento_id>/testar-conexao/', views.testar_conexao_equipamento_view, name='testar_conexao_equipamento'),
    path('equipamentos/<int:equipamento_id>/alternar-status/', views.alternar_status_equipamento_view, name='alternar_status_equipamento'),
    path('equipamentos/<int:equipamento_id>/excluir/', views.excluir_equipamento_view, name='excluir_equipamento'),
    path('equipamentos/sincronizar-todos/', views.sincronizar_todos_view, name='sincronizar_todos'),
    path('equipamentos/<int:equipamento_id>/sincronizar/', views.sincronizar_equipamento_view, name='sincronizar_equipamento'),

    path('marcacoes/', views.listar_marcacoes_view, name='listar_marcacoes'),

    path('horarios/', views.listar_horarios_view, name='listar_horarios'),
    path('horarios/novo/', views.cadastrar_horario_view, name='cadastrar_horario'),
    path('horarios/<int:horario_id>/editar/', views.editar_horario_view, name='editar_horario'),
    path('horarios/<int:horario_id>/excluir/', views.excluir_horario_view, name='excluir_horario'),

    path('escalas/', views.listar_escalas_view, name='listar_escalas'),
    path('escalas/nova/', views.cadastrar_escala_view, name='cadastrar_escala'),
    path('escalas/<int:escala_id>/editar/', views.editar_escala_view, name='editar_escala'),
    path('escalas/<int:escala_id>/excluir/', views.excluir_escala_view, name='excluir_escala'),
]
