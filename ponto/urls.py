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
    path('equipamentos/enviar-todos/', views.enviar_todos_view, name='enviar_todos'),
    path('equipamentos/receber-todos/', views.receber_todos_view, name='receber_todos'),
    path('equipamentos/<int:equipamento_id>/enviar/', views.enviar_equipamento_view, name='enviar_equipamento'),
    path('equipamentos/<int:equipamento_id>/receber/', views.receber_equipamento_view, name='receber_equipamento'),

    path('marcacoes/', views.listar_marcacoes_view, name='listar_marcacoes'),

    path('horarios/', views.listar_horarios_view, name='listar_horarios'),
    path('horarios/novo/', views.cadastrar_horario_view, name='cadastrar_horario'),
    path('horarios/<int:horario_id>/editar/', views.editar_horario_view, name='editar_horario'),
    path('horarios/<int:horario_id>/excluir/', views.excluir_horario_view, name='excluir_horario'),

    path('calculo/', views.listar_calculo_view, name='listar_calculo'),
    path('calculo/<int:funcionario_id>/atribuir-jornada/', views.atribuir_jornada_view, name='atribuir_jornada'),
    path('calculo/<int:funcionario_id>/<str:data>/ajustar/', views.ajustar_marcacao_view, name='ajustar_marcacao'),

    path('feriados/', views.listar_feriados_view, name='listar_feriados'),
    path('feriados/novo/', views.cadastrar_feriado_view, name='cadastrar_feriado'),
    path('feriados/<int:feriado_id>/excluir/', views.excluir_feriado_view, name='excluir_feriado'),
]
