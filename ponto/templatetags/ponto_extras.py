from django import template

register = template.Library()


@register.filter
def horas(segundos):
    """Formata segundos como '[-]HH:MM' para exibir saldo/duração no espelho de ponto."""
    if segundos is None:
        return '—'

    sinal = '-' if segundos < 0 else ''
    total_minutos = abs(segundos) // 60
    return f'{sinal}{total_minutos // 60:02d}:{total_minutos % 60:02d}'
