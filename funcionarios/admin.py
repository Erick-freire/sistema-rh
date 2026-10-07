from django.contrib import admin
from .models import (
    Atestado, Setor, Funcionario, Usuario, TipoDocumento, Documento, TermoComodato, ItemComodato, ItemCatalogoComodato,
    ItemCatalogoEPI, FichaEPI, ItemEPI, OcorrenciaEPI, MatrizEpiSetor,
)


@admin.register(Atestado)
class AtestadoAdmin(admin.ModelAdmin):
    list_display = ('funcionario', 'data_inicio', 'data_fim', 'motivo')
    list_filter = ('data_inicio',)
    search_fields = ('funcionario__nome',)


@admin.register(TipoDocumento)
class TipoDocumentoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'obrigatorio', 'eh_contrato', 'condicao', 'permite_varios')
    list_filter = ('obrigatorio', 'eh_contrato', 'condicao', 'permite_varios')


@admin.register(Documento)
class DocumentoAdmin(admin.ModelAdmin):
    list_display = ('funcionario', 'tipo_documento', 'status', 'data_upload')
    list_filter = ('status', 'tipo_documento')
    
@admin.register(ItemCatalogoComodato)
class ItemCatalogoComodatoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'modalidade', 'ativo')
    list_filter = ('modalidade', 'ativo')
    search_fields = ('nome',)


class ItemComodatoInline(admin.TabularInline):
    model = ItemComodato
    extra = 1
    fields = ('numero_serie', 'item_catalogo', 'descricao', 'tamanho', 'quantidade', 'valor_unitario', 'devolvido')


class TermoComodatoAdmin(admin.ModelAdmin):
    list_display = ['funcionario', 'modalidade', 'status', 'data_emprestimo']
    inlines = [ItemComodatoInline]


@admin.register(ItemCatalogoEPI)
class ItemCatalogoEPIAdmin(admin.ModelAdmin):
    list_display = ('nome', 'numero_ca', 'validade_ca', 'ativo')
    list_filter = ('ativo',)
    search_fields = ('nome', 'numero_ca')


class ItemEPIInline(admin.TabularInline):
    model = ItemEPI
    extra = 1
    fields = ('item_catalogo', 'quantidade', 'data_vencimento', 'devolvido')


@admin.register(FichaEPI)
class FichaEPIAdmin(admin.ModelAdmin):
    list_display = ('funcionario', 'status', 'data_entrega')
    list_filter = ('status',)
    search_fields = ('funcionario__nome',)
    inlines = [ItemEPIInline]


@admin.register(OcorrenciaEPI)
class OcorrenciaEPIAdmin(admin.ModelAdmin):
    list_display = ('funcionario', 'data_ocorrencia', 'item_catalogo', 'tecnico')
    list_filter = ('data_ocorrencia',)
    search_fields = ('funcionario__nome', 'descricao')


@admin.register(MatrizEpiSetor)
class MatrizEpiSetorAdmin(admin.ModelAdmin):
    list_display = ('setor', 'item_catalogo', 'quantidade_padrao')
    list_filter = ('setor',)


admin.site.register(Setor)
admin.site.register(Funcionario)
@admin.register(Usuario)
class UsuarioAdmin(admin.ModelAdmin):
    list_display = ('user', 'funcionario', 'papel', 'login_gestor')
    list_filter = ('papel',)
    search_fields = ('funcionario__nome', 'user__username', 'login_gestor')
admin.site.register(TermoComodato, TermoComodatoAdmin)
