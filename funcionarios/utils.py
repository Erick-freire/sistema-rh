from decimal import Decimal

from num2words import num2words

MESES = [
    'janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho',
    'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
]


def formatar_cpf(cpf):
    return f'{cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}'


def data_por_extenso(data):
    return f'{data.day} de {MESES[data.month - 1]} de {data.year}'


def formatar_moeda(valor):
    texto = f'{valor:,.2f}'
    return 'R$ ' + texto.replace(',', '_').replace('.', ',').replace('_', '.')


def salario_por_extenso(valor):
    valor = Decimal(valor).quantize(Decimal('0.01'))
    reais = int(valor)
    centavos = int((valor - reais) * 100)

    partes = []
    if reais > 0 or centavos == 0:
        sufixo_reais = 'real' if reais == 1 else 'reais'
        extenso_reais = num2words(reais, lang='pt_BR')
        ultima_palavra = extenso_reais.split()[-1]
        conector = ' de ' if ultima_palavra in ('milhão', 'milhões', 'bilhão', 'bilhões') else ' '
        partes.append(f'{extenso_reais}{conector}{sufixo_reais}')
    if centavos > 0:
        sufixo_centavos = 'centavo' if centavos == 1 else 'centavos'
        partes.append(f'{num2words(centavos, lang="pt_BR")} {sufixo_centavos}')

    return ' e '.join(partes)
