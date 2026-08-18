from django.contrib import admin

from .models import (
    AjustePonto, Equipamento, Escala, EscalaItem, Feriado, FuncionarioJornada, Horario, HorarioDia,
    MarcacaoRelogio, SincronizacaoFuncionario,
)


@admin.register(Equipamento)
class EquipamentoAdmin(admin.ModelAdmin):
    list_display = ('nome', 'ip', 'porta', 'modelo', 'ativo', 'status_conexao', 'ultima_comunicacao')
    list_filter = ('ativo', 'status_conexao')
    # senha_admin propositalmente fora de `fields`: nunca deve aparecer em nenhuma tela, nem no admin.
    fields = ('nome', 'ip', 'porta', 'modelo', 'local', 'numero_serie', 'ativo', 'usuario_admin')
    readonly_fields = ()


@admin.register(SincronizacaoFuncionario)
class SincronizacaoFuncionarioAdmin(admin.ModelAdmin):
    list_display = ('funcionario', 'equipamento', 'status', 'acao_pendente', 'tentativas', 'atualizado_em')
    list_filter = ('status', 'acao_pendente', 'equipamento')
    search_fields = ('funcionario__nome', 'funcionario__matricula')
    readonly_fields = ('criado_em', 'atualizado_em')


@admin.register(MarcacaoRelogio)
class MarcacaoRelogioAdmin(admin.ModelAdmin):
    list_display = ('funcionario', 'equipamento', 'momento', 'tipo_evento', 'coletado_em')
    list_filter = ('equipamento', 'tipo_evento')
    search_fields = ('funcionario__nome', 'funcionario__matricula', 'id_evento_dispositivo')
    date_hierarchy = 'momento'

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AjustePonto)
class AjustePontoAdmin(admin.ModelAdmin):
    list_display = ('funcionario', 'data', 'tipo_ajuste', 'usuario', 'criado_em')
    list_filter = ('tipo_ajuste',)
    search_fields = ('funcionario__nome', 'funcionario__matricula')


class HorarioDiaInline(admin.TabularInline):
    model = HorarioDia
    extra = 0
    max_num = 7


@admin.register(Horario)
class HorarioAdmin(admin.ModelAdmin):
    list_display = ('numero', 'nome', 'considerar_feriados')
    search_fields = ('numero', 'nome')
    inlines = [HorarioDiaInline]


class EscalaItemInline(admin.TabularInline):
    model = EscalaItem
    extra = 0


@admin.register(Escala)
class EscalaAdmin(admin.ModelAdmin):
    list_display = ('numero', 'descricao', 'tamanho_ciclo', 'data_base')
    search_fields = ('numero', 'descricao')
    inlines = [EscalaItemInline]


@admin.register(Feriado)
class FeriadoAdmin(admin.ModelAdmin):
    list_display = ('data', 'descricao')


@admin.register(FuncionarioJornada)
class FuncionarioJornadaAdmin(admin.ModelAdmin):
    list_display = ('funcionario', 'data_inicio', 'horario', 'escala')
    search_fields = ('funcionario__nome', 'funcionario__matricula')
    list_filter = ('horario', 'escala')
