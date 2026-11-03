from django.contrib import admin

from .models import (
    Fechamento, LinhaFechamento, MembroPremioPrograma, MembroPremioSafra, RegraPremio, RegraPremioPrograma,
    RegraPremioSafra,
)


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


class MembroPremioProgramaInline(admin.TabularInline):
    model = MembroPremioPrograma
    raw_id_fields = ('funcionario',)
    readonly_fields = ('incluido_em', 'incluido_por')
    extra = 0


@admin.register(RegraPremioPrograma)
class RegraPremioProgramaAdmin(admin.ModelAdmin):
    list_display = ('cargo', 'valor', 'atualizado_em')
    search_fields = ('cargo', 'membros__funcionario__nome')
    inlines = [MembroPremioProgramaInline]


class MembroPremioSafraInline(admin.TabularInline):
    model = MembroPremioSafra
    raw_id_fields = ('funcionario',)
    readonly_fields = ('incluido_em', 'incluido_por')
    extra = 0


@admin.register(RegraPremioSafra)
class RegraPremioSafraAdmin(admin.ModelAdmin):
    list_display = ('nome', 'percentual', 'vigente_de', 'vigente_ate', 'atualizado_em')
    search_fields = ('nome', 'membros__funcionario__nome')
    inlines = [MembroPremioSafraInline]
