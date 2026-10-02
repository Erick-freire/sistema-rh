from django import template

register = template.Library()

# Ordinais femininos ("Cláusula Primeira", "Cláusula Segunda"...). Lista fechada
# em vez de num2words porque o `to='ordinal'` do num2words devolve a forma
# masculina ("primeiro"), e a concordância aqui é com "cláusula".
ORDINAIS_FEMININOS = [
    'Primeira', 'Segunda', 'Terceira', 'Quarta', 'Quinta', 'Sexta', 'Sétima',
    'Oitava', 'Nona', 'Décima', 'Décima Primeira', 'Décima Segunda',
    'Décima Terceira', 'Décima Quarta', 'Décima Quinta', 'Décima Sexta',
    'Décima Sétima', 'Décima Oitava', 'Décima Nona', 'Vigésima',
    'Vigésima Primeira', 'Vigésima Segunda', 'Vigésima Terceira',
    'Vigésima Quarta', 'Vigésima Quinta', 'Vigésima Sexta', 'Vigésima Sétima',
    'Vigésima Oitava', 'Vigésima Nona', 'Trigésima', 'Trigésima Primeira',
    'Trigésima Segunda', 'Trigésima Terceira', 'Trigésima Quarta',
    'Trigésima Quinta', 'Trigésima Sexta', 'Trigésima Sétima',
    'Trigésima Oitava', 'Trigésima Nona', 'Quadragésima',
]


@register.filter
def ordinal_clausula(numero):
    """1 -> "Primeira". Acima da lista, cai no formato numérico ("41ª")."""
    try:
        indice = int(numero) - 1
    except (TypeError, ValueError):
        return numero
    if 0 <= indice < len(ORDINAIS_FEMININOS):
        return ORDINAIS_FEMININOS[indice]
    return f'{numero}ª'


@register.filter
def nome_mes(numero):
    """1 -> "Janeiro". Usado onde só o número da competência está à mão."""
    from terceiros.models import NOME_DOS_MESES

    try:
        return NOME_DOS_MESES[int(numero)]
    except (TypeError, ValueError, KeyError):
        return numero
