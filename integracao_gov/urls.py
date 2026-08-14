from django.urls import path

from . import views

urlpatterns = [
    path('minhas-assinaturas/', views.minhas_assinaturas_view, name='minhas_assinaturas'),
    path('login/<int:solicitacao_id>/', views.iniciar_login_gov_view, name='iniciar_login_gov'),
    path('callback/', views.callback_gov_view, name='callback_gov'),
    path('webhook/status/', views.webhook_status_assinatura_view, name='webhook_status_assinatura'),
]
