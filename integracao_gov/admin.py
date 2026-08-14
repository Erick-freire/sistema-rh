from django.contrib import admin

from .models import SolicitacaoAssinatura, TokenGovBr


@admin.register(TokenGovBr)
class TokenGovBrAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'expira_em', 'criado_em')
    # access_token/refresh_token propositalmente fora de `fields`: nunca devem aparecer em nenhuma tela, nem no admin.
    fields = ('usuario', 'expira_em')


@admin.register(SolicitacaoAssinatura)
class SolicitacaoAssinaturaAdmin(admin.ModelAdmin):
    list_display = ('funcionario', 'status', 'solicitado_por', 'solicitado_em', 'assinado_em')
    list_filter = ('status',)
    readonly_fields = ('solicitado_em',)
