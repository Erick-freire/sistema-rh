from django.contrib import admin

from .models import (
    ClausulaContratual,
    ColaboradorTerceiro,
    ContratoTerceiro,
    DocumentoColaborador,
    DocumentoEmpresaTerceira,
    EmpresaTerceira,
    ResponsavelTerceiro,
)


@admin.register(EmpresaTerceira)
class EmpresaTerceiraAdmin(admin.ModelAdmin):
    list_display = ('razao_social', 'cnpj', 'cidade', 'estado', 'status', 'criado_em')
    list_filter = ('status', 'estado')
    search_fields = ('razao_social', 'nome_fantasia', 'cnpj')


@admin.register(ResponsavelTerceiro)
class ResponsavelTerceiroAdmin(admin.ModelAdmin):
    list_display = ('nome', 'cpf', 'empresa', 'senha_provisoria')
    search_fields = ('nome', 'cpf', 'empresa__razao_social')


@admin.register(ClausulaContratual)
class ClausulaContratualAdmin(admin.ModelAdmin):
    list_display = ('titulo', 'ordem_sugerida', 'ativa')
    list_filter = ('ativa',)
    search_fields = ('titulo', 'texto')


@admin.register(ContratoTerceiro)
class ContratoTerceiroAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'prazo_meses', 'gerado_por', 'gerado_em')
    list_filter = ('gerado_em',)
    search_fields = ('empresa__razao_social',)


@admin.register(ColaboradorTerceiro)
class ColaboradorTerceiroAdmin(admin.ModelAdmin):
    list_display = ('nome', 'cpf', 'funcao', 'empresa', 'id_catraca')
    search_fields = ('nome', 'cpf', 'empresa__razao_social')


@admin.register(DocumentoColaborador)
class DocumentoColaboradorAdmin(admin.ModelAdmin):
    list_display = ('colaborador', 'tipo', 'competencia_display', 'status', 'enviado_em')
    list_filter = ('tipo', 'status', 'competencia_ano', 'competencia_mes')
    search_fields = ('colaborador__nome',)


@admin.register(DocumentoEmpresaTerceira)
class DocumentoEmpresaTerceiraAdmin(admin.ModelAdmin):
    list_display = ('empresa', 'tipo', 'competencia_display', 'status', 'enviado_em')
    list_filter = ('tipo', 'status', 'competencia_ano', 'competencia_mes')
    search_fields = ('empresa__razao_social',)
