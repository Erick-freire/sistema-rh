from django import template
from django.utils import timezone
from django.utils.html import format_html

from funcionarios.utils import formatar_moeda

register = template.Library()


@register.filter
def moeda(valor):
    return '—' if valor is None else formatar_moeda(valor)


@register.filter
def horas(segundos):
    """Formata segundos como '[-]HH:MM' para exibir saldo/duração no espelho de ponto."""
    if segundos is None:
        return '—'

    sinal = '-' if segundos < 0 else ''
    total_minutos = abs(segundos) // 60
    return f'{sinal}{total_minutos // 60:02d}:{total_minutos % 60:02d}'


_MARCA_DIA_TURNO = '<sup title="{}" style="color: var(--color-text-muted); font-weight: 600;">{}</sup>'


@register.filter
def marca_dia_turno(momento, dia):
    """
    "+1"/"−1" ao lado de uma batida que caiu em outro dia de calendário (no fuso
    local) que não o dia do turno -- ex.: a saída às 06:00 de um turno que começou
    às 18:00. Vazio quando a batida é do próprio dia do turno.
    """
    if not momento or not dia:
        return ''
    dia_da_batida = timezone.localtime(momento).date()
    if dia_da_batida > dia:
        return format_html(_MARCA_DIA_TURNO, 'Batida no dia seguinte ao do turno', '+1')
    if dia_da_batida < dia:
        return format_html(_MARCA_DIA_TURNO, 'Batida no dia anterior ao do turno', '−1')
    return ''
