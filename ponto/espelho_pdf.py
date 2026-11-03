"""
Espelho de ponto em PDF, para um funcionário ou para o setor inteiro (uma
página por pessoa). Usa o mesmo cálculo da tela Espelho e cálculo
(`calculos.calcular_periodo`), então os números são os mesmos de lá.
"""
import io
import os

from django.conf import settings
from django.template.loader import render_to_string
from django.utils import timezone
from xhtml2pdf import pisa

from funcionarios.models import Funcionario
from funcionarios.utils import DIAS_SEMANA, formatar_cpf

from .calculos import calcular_periodo
from .models import FuncionarioJornada

# Larguras em cm do A4 em pé (18,6 cm úteis). O xhtml2pdf não ajusta as colunas
# pelo conteúdo e ignora a largura quando a célula tem padding lateral -- por
# isso as larguras fixas e só padding vertical no template.
LARGURA_UTIL_CM = 18.6
LARGURA_DATA_CM = 1.9
# Cabe "06:10+1*" sem quebrar a linha (linha quebrada empurra o espelho para uma segunda página).
LARGURA_BATIDA_CM = 1.05
LARGURAS_TOTAIS_CM = [1.1, 1.0, 1.3, 1.0, 1.0]  # normais, noturno, faltas, extra 50%, extra 100%
LARGURA_MINIMA_OBS_CM = 1.8


def funcionarios_que_batem_ponto():
    """
    Quem entra no espelho de um setor: os ativos, sem gestor (cargo de
    confiança, não bate ponto -- igual ao Ponto da Equipe) e sem os gestores
    placeholder da importação.
    """
    return (
        Funcionario.objects
        .filter(status='ativo')
        .exclude(usuario__papel='gestor')
        .exclude(matricula__startswith='PENDENTE-')
    )


def funcionarios_do_setor(setor):
    return funcionarios_que_batem_ponto().filter(setor=setor).select_related('setor').order_by('nome')


def _horas(segundos):
    if not segundos:
        return ''
    minutos = segundos // 60
    return f'{minutos // 60:02d}:{minutos % 60:02d}'


def _batida(evento, dia):
    """'07:58', com '*' se foi lançada à mão e '+1' se caiu no dia seguinte ao do turno."""
    if evento is None:
        return ''
    momento = timezone.localtime(evento.momento)
    texto = momento.strftime('%H:%M')
    # Sem espaço e com hífen comum: a fonte do PDF não tem o sinal de menos (−).
    if momento.date() > dia:
        texto += '+1'
    elif momento.date() < dia:
        texto += '-1'
    return texto + ('*' if evento.manual else '')


def _observacao(dia):
    if dia['ferias']:
        return 'Férias'
    if dia['atestado']:
        return 'Atestado'
    if dia['sem_jornada']:
        return 'Sem jornada'
    if dia['feriado']:
        return 'Feriado'
    if dia['folga']:
        return 'Folga' if not dia['marcacoes'] else 'Folga trabalhada'
    if dia['dia_falta']:
        return 'Dia falta'
    if dia['marcacao_impar']:
        return 'Marcação ímpar'
    return ''


def dados_do_espelho(funcionario, data_inicio, data_fim):
    """Tudo o que o template precisa para a página de um funcionário."""
    resultado = calcular_periodo(funcionario, data_inicio, data_fim)
    pares = resultado['pares_no_periodo']
    dias = []
    for dia in resultado['dias']:
        batidas = []
        for entrada, saida in dia['pares']:
            batidas += [_batida(entrada, dia['data']), _batida(saida, dia['data'])]
        dias.append({
            'data': f'{dia["data"]:%d/%m} {DIAS_SEMANA[dia["data"].weekday()][:3].lower()}',
            'batidas': batidas,
            'normais': _horas(dia['segundos_normais']),
            'noturno': _horas(dia['segundos_noturnos']),
            'faltas': 'Dia falta' if dia['dia_falta'] else _horas(dia['segundos_falta_dia']),
            'extra_50': _horas(dia['segundos_extra_50_dia']),
            'extra_100': _horas(dia['segundos_extra_100_dia']),
            'observacao': _observacao(dia),
            'destaque': dia['folga'] or dia['ferias'] or dia['atestado'] or dia['feriado'],
        })

    totais = resultado['totais']
    jornadas = list(
        FuncionarioJornada.objects.filter(funcionario=funcionario, data_inicio__lte=data_fim)
        .select_related('horario').order_by('-data_inicio')
    )
    # As que valeram em algum dia do período: a vigente no início e as que começaram dentro dele.
    jornadas_do_periodo = [j for j in jornadas if j.data_inicio >= data_inicio] + [
        j for j in jornadas if j.data_inicio < data_inicio
    ][:1]
    # Com muitos pares (até 5), as batidas encolhem para a observação não sumir.
    sobra = LARGURA_UTIL_CM - LARGURA_DATA_CM - sum(LARGURAS_TOTAIS_CM) - LARGURA_MINIMA_OBS_CM
    largura_batida = min(LARGURA_BATIDA_CM, sobra / (2 * pares))
    largura_obs = LARGURA_UTIL_CM - LARGURA_DATA_CM - sum(LARGURAS_TOTAIS_CM) - largura_batida * 2 * pares
    return {
        'funcionario': funcionario,
        'cpf': formatar_cpf(funcionario.cpf) if len(funcionario.cpf or '') == 11 else funcionario.cpf,
        'jornadas': sorted(jornadas_do_periodo, key=lambda j: j.data_inicio),
        'numeros_dos_pares': range(1, pares + 1),
        'dias': dias,
        'totais': {
            'normais': _horas(sum(d['segundos_normais'] for d in resultado['dias'])) or '00:00',
            'noturno': _horas(totais['noturno_segundos']) or '00:00',
            'noturno_convertido': _horas(totais['noturno_convertido_segundos']) or '00:00',
            'extra_50': _horas(totais['extra_50_segundos']) or '00:00',
            'extra_100': _horas(totais['extra_100_segundos']) or '00:00',
            'faltas': _horas(totais['falta_segundos']) or '00:00',
            'dias_falta': totais['dias_falta'],
            'dsr_perdidos': totais['dsr_perdidos'],
        },
        'larguras': {
            'data': LARGURA_DATA_CM,
            'batida': round(largura_batida, 2),
            'totais': LARGURAS_TOTAIS_CM,
            'observacao': round(largura_obs, 2),
        },
    }


def gerar_pdf(funcionarios, data_inicio, data_fim, gerado_por):
    """Bytes do PDF com uma página (ou mais, se o período for longo) por funcionário; None se o xhtml2pdf falhar."""
    html = render_to_string('ponto/espelho_pdf.html', {
        'espelhos': [dados_do_espelho(funcionario, data_inicio, data_fim) for funcionario in funcionarios],
        'data_inicio': data_inicio,
        'data_fim': data_fim,
        'gerado_em': timezone.localtime(),
        'gerado_por': gerado_por,
        'caminho_logo': os.path.join(settings.BASE_DIR, 'static', 'img', 'logo-sotrigo.png'),
    })
    saida = io.BytesIO()
    if pisa.CreatePDF(html, dest=saida, encoding='utf-8').err:
        return None
    return saida.getvalue()
