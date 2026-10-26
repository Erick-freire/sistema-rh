"""
As colunas do fechamento, na ordem e com o cabeçalho exato da planilha que o
escritório de contabilidade recebe (modelo: FOLHA SETEMBRO 2026 - CONTABILIDADE).
O cabeçalho traz o código da rubrica no sistema deles: "H.E. 50%|35|R" é a
rubrica 35, R = referência (horas ou dias) e V = valor em R$.

Uma lista só serve a tela, o .xlsx e o PDF, para os três nunca saírem
diferentes. Cada tipo sabe exibir, ler o que o RH digitou e exportar.

Horas: guardadas em minutos; no arquivo vão em horas.minutos, como no
modelo (6h20 = 6.20 -- os minutos nunca passam de .59).
"""
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

SEIS_PORCENTO = Decimal('0.06')


@dataclass(frozen=True)
class Coluna:
    campo: str
    cabecalho: str  # texto exato do modelo (vai para o .xlsx)
    rotulo: str  # nome curto, para a tela e o PDF
    tipo: str  # cadastro, moeda, horas, dias, sim, sim_nao, percentual, vale_transporte
    origem: str  # cadastro, ponto, regra (regras de prêmios) ou rh
    largura: float  # largura da coluna no .xlsx (a mesma do modelo)
    rubrica: str = ''
    # Só percentual: True exporta como fração formatada em % (30 -> 0,3 = 30%),
    # False exporta o número como está (10). É como cada coluna vem no modelo.
    fracao_no_xlsx: bool = False

    @property
    def editavel(self):
        return self.origem != 'cadastro'


COLUNAS = [
    Coluna('codigo_empresa', 'EMP', 'EMP', 'cadastro', 'cadastro', 9.11),
    Coluna('matricula', 'MATRÍCULA', 'Matr.', 'cadastro', 'cadastro', 9.11),
    Coluna('nome', 'FUNCIONARIOS', 'Funcionário', 'cadastro', 'cadastro', 41.44),
    Coluna('descontos', 'DESCONTOS (VALOR)|343|v', 'Descontos', 'moeda', 'rh', 19.11, '343'),
    Coluna('decimo_terceiro', '13 SA0', '13º', 'sim', 'rh', 11.33),
    Coluna('adicional_noturno_minutos', 'ADC NOT|1361|R', 'Adic. not.', 'horas', 'ponto', 16.55, '1361'),
    Coluna('he_50_minutos', 'H.E. 50%|35|R', 'H.E. 50%', 'horas', 'ponto', 18.33, '35'),
    Coluna('he_100_minutos', 'H.E. 100%|049|R', 'H.E. 100%', 'horas', 'ponto', 20.66, '049'),
    Coluna('premio_programa', 'PRÊMIOS – PROGRAMA EMPRESA|1591|V', 'Prêmio programa', 'moeda', 'regra', 21.44, '1591'),
    Coluna('premio_bonus', 'PRÊMIOS – BÔNUS|1592|V', 'Prêmio bônus', 'moeda', 'rh', 19.44, '1592'),
    Coluna('premio_incentivo', 'PREMIO INCENTIVO | 1870| V', 'Prêmio incentivo', 'moeda', 'regra', 17.89, '1870'),
    Coluna('premio_safra', 'PREMIO BONUS \nSAFRA (10%)', 'Bônus safra', 'percentual', 'regra', 13.0),
    Coluna('reembolso_despesas', 'REEMBOLSO DE DESPESAS|1497|v', 'Reembolso', 'moeda', 'rh', 21.44, '1497'),
    Coluna('comissao', 'COMI|90|V', 'Comissão', 'moeda', 'rh', 15.89, '90'),
    Coluna('adiantamento_comissao', 'ADI COMI|314|V', 'Adiant. comissão', 'moeda', 'rh', 16.89, '314'),
    Coluna('atestados_dias', 'ATESTADOS', 'Atestados (dias)', 'dias', 'ponto', 16.33),
    Coluna('faltas_minutos', 'FALTAS HORAS |226| r', 'Faltas (horas)', 'horas', 'ponto', 12.44, '226'),
    Coluna('faltas_dias', 'FALTAS|023|R', 'Faltas (dias)', 'dias', 'ponto', 16.0, '023'),
    Coluna('assiduidade', 'ASSIDUIDADE', 'Assiduidade', 'sim_nao', 'ponto', 17.66),
    Coluna('vale_transporte', 'VALE TRANSP', 'Vale transp.', 'vale_transporte', 'rh', 12.55),
    Coluna('periculosidade', 'PERICULOSIDADE', 'Periculosidade', 'percentual', 'rh', 16.11, fracao_no_xlsx=True),
]
COLUNAS_POR_CAMPO = {coluna.campo: coluna for coluna in COLUNAS}


class ValorInvalido(ValueError):
    """Texto digitado que não dá para converter; a mensagem vai para o RH."""


# ---------- Leitura do que o RH digita ----------

def _decimal_br(texto):
    """'1.234,56', '240,9', '240.9', 'R$ 1.000' -> Decimal com 2 casas; vazio -> None."""
    limpo = (texto or '').replace('R$', '').replace(' ', '').strip()
    if not limpo:
        return None
    if ',' in limpo:
        limpo = limpo.replace('.', '').replace(',', '.')
    elif re.fullmatch(r'\d{1,3}(\.\d{3})+', limpo):
        limpo = limpo.replace('.', '')  # "1.000" é mil, não 1,0
    try:
        valor = Decimal(limpo)
    except InvalidOperation:
        raise ValorInvalido('Valor inválido. Exemplo: 1.234,56.')
    if valor < 0:
        raise ValorInvalido('Informe um valor positivo.')
    return valor.quantize(Decimal('0.01'))


def ler_moeda(texto):
    return _decimal_br(texto)


def ler_horas(texto):
    """
    Minutos a partir de '6:20', '6,20' ou '6.20' (todos 6h20); vazio -> 0.
    Com vírgula ou ponto vale a convenção da planilha (horas.minutos): '6,5' é
    6h50 e '0,3' é 30 min. Com dois-pontos, '6:5' é 6h05, como num relógio.
    """
    limpo = (texto or '').strip()
    if not limpo:
        return 0
    partes = re.fullmatch(r'(\d+)(?:([:.,])(\d{1,2}))?', limpo)
    if not partes:
        raise ValorInvalido('Use horas e minutos. Exemplo: 6:20 ou 6,20.')
    horas, separador, minutos = partes.groups()
    if separador in ('.', ',') and len(minutos) == 1:
        minutos += '0'
    minutos = int(minutos or 0)
    if minutos >= 60:
        raise ValorInvalido('Os minutos vão de 00 a 59.')
    return int(horas) * 60 + minutos


def ler_dias(texto):
    limpo = (texto or '').strip()
    if not limpo:
        return 0
    if not limpo.isdigit():
        raise ValorInvalido('Informe um número inteiro de dias.')
    return int(limpo)


def ler_percentual(texto):
    """'30' ou '30%' -> Decimal('30.00'); vazio -> None."""
    return _decimal_br((texto or '').replace('%', ''))


def ler_vale_transporte(texto):
    """
    (seis_porcento, valor). O desconto do VT é o menor entre 6% do salário e o
    valor creditado pela empresa (regra confirmada com o usuário): o RH digita
    '6%' ou o valor em R$.
    """
    limpo = (texto or '').strip()
    if not limpo:
        return False, None
    if limpo.endswith('%'):
        if _decimal_br(limpo[:-1]) != Decimal('6.00'):
            raise ValorInvalido('O desconto em percentual é sempre 6%. Para outro valor, informe em R$.')
        return True, None
    return False, ler_moeda(limpo)


def ler_sim(texto):
    return (texto or '').strip().lower() in ('1', 'true', 'on', 'sim', 's')


LEITORES = {
    'moeda': ler_moeda,
    'horas': ler_horas,
    'dias': ler_dias,
    'percentual': ler_percentual,
    'sim': ler_sim,
    'sim_nao': ler_sim,
}


def gravar(linha, coluna, texto):
    """Converte o texto digitado e grava no atributo da linha (sem salvar). Pode levantar ValorInvalido."""
    if coluna.tipo == 'vale_transporte':
        linha.vt_seis_porcento, linha.vt_valor = ler_vale_transporte(texto)
        return
    setattr(linha, coluna.campo, LEITORES[coluna.tipo](texto))


# ---------- Exibição (tela e PDF) ----------

def _moeda_br(valor):
    return f'{valor:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def _numero_br(valor):
    """Percentual sem casas decimais desnecessárias: 30, 12,5."""
    texto = f'{valor.normalize():f}' if valor == valor.to_integral() else f'{valor:.2f}'.rstrip('0')
    return texto.replace('.', ',')


def horas_texto(minutos):
    return f'{minutos // 60}:{minutos % 60:02d}' if minutos else ''


def valor_bruto(linha, coluna):
    if coluna.tipo == 'vale_transporte':
        return (linha.vt_seis_porcento, linha.vt_valor)
    return getattr(linha, coluna.campo)


def exibir(coluna, valor):
    """Texto da célula. Zero e vazio ficam em branco, como na planilha."""
    if coluna.tipo == 'cadastro':
        return '' if valor is None else str(valor)
    if coluna.tipo == 'vale_transporte':
        seis_porcento, quantia = valor
        if seis_porcento:
            return '6%'
        return _moeda_br(quantia) if quantia else ''
    if coluna.tipo == 'horas':
        return horas_texto(valor)
    if coluna.tipo in ('sim', 'sim_nao'):
        return 'SIM' if valor else ('NÃO' if coluna.tipo == 'sim_nao' else '')
    if not valor:
        return ''
    if coluna.tipo == 'moeda':
        return _moeda_br(valor)
    if coluna.tipo == 'percentual':
        return f'{_numero_br(valor)}%'
    return str(valor)


def exibir_da_linha(linha, coluna):
    return exibir(coluna, valor_bruto(linha, coluna))


# ---------- .xlsx ----------

def xlsx(coluna, valor):
    """(valor da célula, formato do número) como no modelo."""
    if coluna.tipo == 'cadastro':
        if coluna.campo == 'matricula' and valor and str(valor).isdigit():
            return int(valor), 'General'
        return valor, 'General'
    if coluna.tipo == 'vale_transporte':
        seis_porcento, quantia = valor
        if seis_porcento:
            return float(SEIS_PORCENTO), '0%'
        return (float(quantia), '#,##0.00') if quantia else (None, 'General')
    if coluna.tipo == 'horas':
        # 6h20 -> 6.20. Formato com duas casas para "6,20" não ser lido como 6h02.
        return (round(valor // 60 + (valor % 60) / 100, 2), '0.00') if valor else (None, 'General')
    if coluna.tipo == 'sim':
        return ('SIM' if valor else None), 'General'
    if coluna.tipo == 'sim_nao':
        return ('SIM' if valor else 'NÃO'), 'General'
    if not valor:
        return None, 'General'
    if coluna.tipo == 'moeda':
        return float(valor), '#,##0.00'
    if coluna.tipo == 'percentual':
        if coluna.fracao_no_xlsx:
            return float(valor / 100), '0%'
        return float(valor), 'General'
    return valor, 'General'
