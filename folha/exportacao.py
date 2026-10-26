"""Os arquivos que vão para a contabilidade: .xlsx no layout do modelo e PDF."""
import io
import os

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from xhtml2pdf import pisa

from . import colunas

# Estilo do modelo: cabeçalho e coluna de nomes em azul com letra branca em
# negrito, demais células centralizadas, todas com borda fina.
_AZUL = PatternFill('solid', fgColor='4472C4')
_BORDA = Border(*(Side(style='thin'),) * 4)
_FONTE_AZUL = Font(name='Calibri', size=10, bold=True, color='FFFFFF')
_FONTE = Font(name='Calibri', size=11)


def nome_arquivo(fechamento, extensao):
    previa = '' if fechamento.status == 'fechado' else ' - PREVIA'
    return (
        f'FOLHA {fechamento.competencia_extenso.split("/")[0].upper()} {fechamento.competencia.year}'
        f' - CONTABILIDADE{previa}.{extensao}'
    )


def gerar_xlsx(fechamento):
    planilha = Workbook()
    aba = planilha.active
    aba.title = 'Planilha1'

    for indice, coluna in enumerate(colunas.COLUNAS, start=1):
        celula = aba.cell(row=1, column=indice, value=coluna.cabecalho)
        celula.font, celula.fill, celula.border = _FONTE_AZUL, _AZUL, _BORDA
        celula.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
        aba.column_dimensions[celula.column_letter].width = coluna.largura
    aba.row_dimensions[1].height = 72

    for numero_linha, linha in enumerate(fechamento.linhas.all(), start=2):
        for indice, coluna in enumerate(colunas.COLUNAS, start=1):
            valor, formato = colunas.xlsx(coluna, colunas.valor_bruto(linha, coluna))
            celula = aba.cell(row=numero_linha, column=indice, value=valor)
            celula.number_format = formato
            celula.border = _BORDA
            if coluna.campo == 'nome':
                celula.font, celula.fill = _FONTE_AZUL, _AZUL
                celula.alignment = Alignment(vertical='center')
            else:
                celula.font = _FONTE
                celula.alignment = Alignment(horizontal='center')

    aba.freeze_panes = 'D2'
    aba.auto_filter.ref = aba.dimensions
    aba.page_setup.orientation = 'landscape'

    saida = io.BytesIO()
    planilha.save(saida)
    return saida.getvalue()


# O xhtml2pdf não calcula a largura das colunas pelo conteúdo: sem largura
# fixa ele espreme tudo. Em cm; somam ~27,9 dos 28,1 úteis do A4 deitado, e
# cada coluna cabe a maior palavra do rótulo ("Periculosidade", "Assiduidade").
_LARGURA_PDF_CM = {
    'codigo_empresa': 0.75, 'matricula': 0.85, 'nome': 5.0, 'periculosidade': 1.7,
    'moeda': 1.25, 'horas': 1.05, 'dias': 1.1, 'sim': 0.9, 'sim_nao': 1.35, 'percentual': 1.0, 'vale_transporte': 1.2,
}


def _largura_pdf(coluna):
    return _LARGURA_PDF_CM.get(coluna.campo) or _LARGURA_PDF_CM[coluna.tipo]


def _totais(linhas):
    """Soma de cada coluna de valor, horas ou dias (as demais ficam em branco no rodapé do PDF)."""
    totais = []
    for coluna in colunas.COLUNAS:
        if coluna.tipo in ('moeda', 'horas', 'dias'):
            soma = sum((getattr(linha, coluna.campo) or 0) for linha in linhas)
            totais.append(colunas.exibir(coluna, soma))
        else:
            totais.append('')
    return totais


def gerar_pdf(fechamento, gerado_por):
    """Bytes do PDF (A4 deitado), ou None se o xhtml2pdf falhar. `gerado_por` é o nome que vai no rodapé."""
    linhas = list(fechamento.linhas.all())
    contexto = {
        'fechamento': fechamento,
        'cabecalhos': [(coluna, _largura_pdf(coluna)) for coluna in colunas.COLUNAS],
        'linhas': [[colunas.exibir_da_linha(linha, coluna) for coluna in colunas.COLUNAS] for linha in linhas],
        'totais': _totais(linhas),
        'gerado_em': timezone.localtime(),
        'gerado_por': gerado_por,
        'caminho_logo': os.path.join(settings.BASE_DIR, 'static', 'img', 'logo-sotrigo.png'),
    }
    html = render_to_string('folha/pdf.html', contexto)
    saida = io.BytesIO()
    if pisa.CreatePDF(html, dest=saida, encoding='utf-8').err:
        return None
    return saida.getvalue()
