from django.contrib import admin

from .models import Fechamento, LinhaFechamento, RegraPremio


@admin.register(Fechamento)
class FechamentoAdmin(admin.ModelAdmin):
    list_display = ('competencia', 'status', 'criado_em', 'fechado_em')
    list_filter = ('status',)


@admin.register(LinhaFechamento)
class LinhaFechamentoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'matricula', 'fechamento')
    list_filter = ('fechamento',)
    search_fields = ('nome', 'matricula')


@admin.register(RegraPremio)
class RegraPremioAdmin(admin.ModelAdmin):
    list_display = ('tipo', 'setor', 'cargo', 'funcionario', 'valor', 'vigente_de', 'vigente_ate')
    list_filter = ('tipo', 'setor')
    search_fields = ('cargo', 'funcionario__nome')
