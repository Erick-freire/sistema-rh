from django.contrib import admin
from .models import Atestado, Setor, Funcionario, Usuario, TipoDocumento, Documento, TermoComodato, ItemComodato, ItemCatalogoComodato


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


admin.site.register(Setor)
admin.site.register(Funcionario)
admin.site.register(Usuario)
admin.site.register(TermoComodato, TermoComodatoAdmin)
