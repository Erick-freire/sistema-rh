"""
Motor de cálculo do espelho de ponto: compara o que o funcionário bateu
contra o que deveria ter batido (via Horario/Escala vigente), e aplica as
regras de hora extra/falta e compensação da empresa.

Regras (confirmadas com o usuário):
- Fora da escala 12x36: hora extra +50% em dias úteis (segunda a sábado),
  +100% aos domingos e feriados.
- Na escala 12x36: hora extra sempre +50%, independente do dia.
- Compensação: dentro do período calculado, hora extra compensa falta/atraso
  primeiro pelas horas de 50%; só depois de esgotar essas é que consome as
  de 100%. Só o saldo depois de compensar vira "extra a pagar" ou "falta a
  descontar".

LIMITAÇÃO CONHECIDA: o cálculo processa cada dia isoladamente (marcações
daquele dia calendário). Turno que atravessa a meia-noite (entrada num dia,
saída no dia seguinte) ainda não é tratado -- os 12x36 confirmados até agora
na Sotrigo são diurnos (ex.: horário 1001, 06:00-18:00), então não bateu essa
necessidade ainda. Precisa de ajuste antes de usar para uma escala noturna
que vire o dia.
"""
from datetime import timedelta

from .models import Feriado, FuncionarioJornada, MarcacaoRelogio
from .utils import intervalo_do_dia

# date.isoweekday() (1=segunda...7=domingo) já bate exatamente com HorarioDia.dia_semana.


def _segundos_esperados(horario_dia):
    if horario_dia is None:
        return 0

    total = timedelta()
    for entrada, saida in (
        (horario_dia.entrada1, horario_dia.saida1),
        (horario_dia.entrada2, horario_dia.saida2),
        (horario_dia.entrada3, horario_dia.saida3),
    ):
        if entrada and saida:
            total += _diferenca_horario(entrada, saida)
    return int(total.total_seconds())


def _diferenca_horario(entrada, saida):
    inicio = timedelta(hours=entrada.hour, minutes=entrada.minute, seconds=entrada.second)
    fim = timedelta(hours=saida.hour, minutes=saida.minute, seconds=saida.second)
    if fim < inicio:
        fim += timedelta(days=1)  # jornada atravessa a meia-noite (turno noturno)
    return fim - inicio


def _horario_do_dia(jornada, data):
    """
    Sempre retorna um HorarioDia (ou None se folga) -- é lá que ficam os
    campos de entrada/saída, tanto pra jornada fixa quanto pra escala
    (Escala.horario_para_data devolve um Horario, ainda precisa achar o
    HorarioDia do dia da semana dentro dele).
    """
    if jornada.escala_id:
        horario = jornada.escala.horario_para_data(data)
        em_12x36 = True
    else:
        horario = jornada.horario
        em_12x36 = False

    if horario is None:
        return None, em_12x36

    dia_semana = data.isoweekday()
    return horario.dias.filter(dia_semana=dia_semana).first(), em_12x36


MAX_PARES_EXIBIDOS = 5  # mesmo limite do espelho do Secullum (Ent.1..Ent.5/Saí.1..Saí.5)


def _marcacoes_pareadas(funcionario, data):
    inicio, fim = intervalo_do_dia(data)
    marcacoes = list(
        MarcacaoRelogio.objects
        .filter(funcionario=funcionario, momento__gte=inicio, momento__lt=fim)
        .order_by('momento')
    )

    segundos_trabalhados = 0
    pares = []
    for i in range(0, len(marcacoes) - 1, 2):
        segundos_trabalhados += int((marcacoes[i + 1].momento - marcacoes[i].momento).total_seconds())
        pares.append((marcacoes[i], marcacoes[i + 1]))

    marcacao_impar = len(marcacoes) % 2 == 1
    if marcacao_impar:
        pares.append((marcacoes[-1], None))  # última batida sem par, sinalizada mas visível

    return marcacoes, pares[:MAX_PARES_EXIBIDOS], segundos_trabalhados, marcacao_impar


def _taxa_extra_do_dia(data, em_12x36):
    if em_12x36:
        return 50
    if data.isoweekday() == 7 or Feriado.objects.filter(data=data).exists():
        return 100
    return 50


def calcular_periodo(funcionario, data_inicio, data_fim):
    dias = []
    dia_atual = data_inicio

    while dia_atual <= data_fim:
        jornada = FuncionarioJornada.vigente_em(funcionario, dia_atual)

        if jornada is None:
            dias.append({
                'data': dia_atual, 'sem_jornada': True, 'marcacoes': [], 'pares': [],
                'segundos_esperados': 0, 'segundos_trabalhados': 0, 'saldo_segundos': 0,
                'segundos_normais': 0, 'segundos_falta_dia': 0, 'segundos_extra_50_dia': 0, 'segundos_extra_100_dia': 0,
                'marcacao_impar': False, 'taxa_extra': None, 'folga': False,
            })
            dia_atual += timedelta(days=1)
            continue

        horario_dia, em_12x36 = _horario_do_dia(jornada, dia_atual)
        segundos_esperados = _segundos_esperados(horario_dia)
        marcacoes, pares, segundos_trabalhados, marcacao_impar = _marcacoes_pareadas(funcionario, dia_atual)
        saldo_segundos = segundos_trabalhados - segundos_esperados
        taxa_extra = _taxa_extra_do_dia(dia_atual, em_12x36)

        dias.append({
            'data': dia_atual,
            'sem_jornada': False,
            # `horario_dia is None` só cobre a folga da escala (EscalaItem sem
            # horário); numa jornada fixa, o HorarioDia do fim de semana existe
            # mas sem nenhum entrada/saída -- `.folga` (a property do model)
            # cobre os dois casos.
            'folga': horario_dia is None or horario_dia.folga,
            'marcacoes': marcacoes,
            'pares': pares,
            'marcacao_impar': marcacao_impar,
            'segundos_esperados': segundos_esperados,
            'segundos_trabalhados': segundos_trabalhados,
            'saldo_segundos': saldo_segundos,
            # "Normais" (igual ao espelho do Secullum) nunca passa do esperado
            # do dia -- o que passar disso é que conta como extra, em separado.
            'segundos_normais': min(segundos_trabalhados, segundos_esperados),
            'segundos_falta_dia': max(0, -saldo_segundos),
            'segundos_extra_50_dia': max(0, saldo_segundos) if taxa_extra == 50 else 0,
            'segundos_extra_100_dia': max(0, saldo_segundos) if taxa_extra == 100 else 0,
            'taxa_extra': taxa_extra,
        })
        dia_atual += timedelta(days=1)

    # Mostra só quantos pares de entrada/saída o período realmente usou (em vez
    # de sempre 5 fixos) -- a tela fica bem mais estreita na maioria dos casos,
    # já que a maioria dos horários usa só 2 ou 3 pares por dia.
    pares_no_periodo = max((len(d['pares']) for d in dias), default=0)
    pares_no_periodo = max(2, min(pares_no_periodo, MAX_PARES_EXIBIDOS))
    for dia in dias:
        dia['pares'] = dia['pares'][:pares_no_periodo] + [(None, None)] * (pares_no_periodo - len(dia['pares']))

    superavit_50 = sum(d['saldo_segundos'] for d in dias if d['saldo_segundos'] > 0 and d['taxa_extra'] == 50)
    superavit_100 = sum(d['saldo_segundos'] for d in dias if d['saldo_segundos'] > 0 and d['taxa_extra'] == 100)
    deficit = -sum(d['saldo_segundos'] for d in dias if d['saldo_segundos'] < 0)

    consumo_50 = min(deficit, superavit_50)
    deficit_restante = deficit - consumo_50
    consumo_100 = min(deficit_restante, superavit_100)

    totais = {
        'extra_50_segundos': superavit_50 - consumo_50,
        'extra_100_segundos': superavit_100 - consumo_100,
        'falta_segundos': deficit_restante - consumo_100,
        'dias_sem_jornada': sum(1 for d in dias if d['sem_jornada']),
        'dias_com_marcacao_impar': sum(1 for d in dias if d['marcacao_impar']),
    }

    return {'dias': dias, 'totais': totais, 'pares_no_periodo': pares_no_periodo}
