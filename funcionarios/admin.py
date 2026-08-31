from django.contrib import admin
from .models import Setor, Funcionario, Usuario, TipoDocumento, Documento


@admin.register(TipoDocumento)
class TipoDocumentoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'obrigatorio', 'eh_contrato', 'condicao', 'permite_varios')
    list_filter = ('obrigatorio', 'eh_contrato', 'condicao', 'permite_varios')


@admin.register(Documento)
class DocumentoAdmin(admin.ModelAdmin):
    list_display = ('funcionario', 'tipo_documento', 'status', 'data_upload')
    list_filter = ('status', 'tipo_documento')


admin.site.register(Setor)
admin.site.register(Funcionario)
admin.site.register(Usuario)
