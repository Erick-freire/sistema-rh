"""
Motor de cálculo do espelho de ponto: compara o que o funcionário bateu
contra o que deveria ter batido (via Horario vigente), e aplica as regras de
hora extra/falta e compensação da empresa.

Regras (confirmadas com o usuário):
- Fora do 12x36: hora extra +50% em dias úteis (segunda a sábado), +100% aos
  domingos e feriados.
- No 12x36: hora extra sempre +50%, independente do dia. Quem trabalha em
  qual dia é decidido pela diferença de dias desde `data_inicio` da jornada
  (que é sempre um dia de trabalho), não pelo dia da semana nem pelo dia do
  mês -- ver `FuncionarioJornada.trabalha_no_dia_12x36`.
- Compensação: dentro do período calculado, hora extra compensa falta/atraso
  primeiro pelas horas de 50%; só depois de esgotar essas é que consome as
  de 100%. Só o saldo depois de compensar vira "extra a pagar" ou "falta a
  descontar".

Turno que atravessa a meia-noite (ex.: 12x36 noturno 18:00-06:00, horário
1003): a batida inteira fica no dia em que o turno COMEÇOU, nunca dividida
entre os dois dias -- é assim que o Secullum já trata (conferido direto no
sistema deles). Ver `_janela_do_dia`.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from .models import AjustePonto, Feriado, FuncionarioJornada, MarcacaoRelogio
from .utils import intervalo_do_dia

# `funcionario.solicitacoes_ferias`/`funcionario.atestados` são relations
# reversas (definidas em `funcionarios.models`) -- não precisa importar os
# models de lá aqui pra usá-las.


def _dias_ferias_aprovadas(funcionario):
    dias = set()
    for solicitacao in funcionario.solicitacoes_ferias.filter(status='aprovada'):
        data = solicitacao.data_solicitada
        while data <= solicitacao.data_fim:
            dias.add(data)
            data += timedelta(days=1)
    return dias


def _dias_atestado(funcionario):
    dias = set()
    for atestado in funcionario.atestados.all():
        data = atestado.data_inicio
        while data <= atestado.data_fim:
            dias.add(data)
            data += timedelta(days=1)
    return dias

# date.isoweekday() (1=segunda...7=domingo) já bate exatamente com HorarioDia.dia_semana.


@dataclass
class _Evento:
    """
    Um ponto no tempo pra fim de pareamento -- tanto faz se veio de uma
    MarcacaoRelogio real ou de um AjustePonto manual, o pareamento é sempre
    por ordem cronológica. `manual` é só pra sinalizar o "*" na tela.
    """
    momento: object
    manual: bool


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
    """Sempre retorna um HorarioDia (ou None se folga) -- é lá que ficam os campos de entrada/saída."""
    horario = jornada.horario
    em_12x36 = horario.eh_12x36

    if em_12x36 and not jornada.trabalha_no_dia_12x36(data):
        return None, em_12x36

    dia_semana = data.isoweekday()
    return horario.dias.filter(dia_semana=dia_semana).first(), em_12x36


def _primeira_entrada(horario_dia):
    for entrada in (horario_dia.entrada1, horario_dia.entrada2, horario_dia.entrada3):
        if entrada:
            return entrada
    return None


def _ultima_saida(horario_dia):
    for saida in (horario_dia.saida3, horario_dia.saida2, horario_dia.saida1):
        if saida:
            return saida
    return None


def _atravessa_meia_noite(horario_dia):
    """Turno que termina "antes" de começar (no relógio) -- ex.: 18:00-06:00 -- vira o dia."""
    if horario_dia is None:
        return False
    primeira, ultima = _primeira_entrada(horario_dia), _ultima_saida(horario_dia)
    if not primeira or not ultima:
        return False
    return ultima < primeira


def _janela_do_dia(data, horario_dia_hoje, horario_dia_ontem):
    """
    (início, fim) aware usada pra buscar as marcações do dia `data`.
    Normalmente é meia-noite a meia-noite local (`intervalo_do_dia`). Mas se
    o turno de HOJE atravessa a meia-noite, a janela vai da hora da primeira
    entrada até a mesma hora do dia seguinte -- assim a madrugada inteira
    (que no relógio já é "amanhã") fica contada no dia em que o turno
    começou, igual o Secullum. E se foi o turno de ONTEM que atravessou a
    meia-noite, a janela de hoje só começa depois que aquele turno termina,
    pra não contar a mesma madrugada nos dois dias.
    """
    if _atravessa_meia_noite(horario_dia_hoje):
        inicio = timezone.make_aware(datetime.combine(data, _primeira_entrada(horario_dia_hoje)))
        fim = inicio + timedelta(days=1)
    else:
        inicio, fim = intervalo_do_dia(data)

    if _atravessa_meia_noite(horario_dia_ontem):
        ontem = data - timedelta(days=1)
        inicio_turno_ontem = timezone.make_aware(datetime.combine(ontem, _primeira_entrada(horario_dia_ontem)))
        inicio = max(inicio, inicio_turno_ontem + timedelta(days=1))

    return inicio, fim


MAX_PARES_EXIBIDOS = 5  # mesmo limite do espelho do Secullum (Ent.1..Ent.5/Saí.1..Saí.5)


def _eventos_do_dia(funcionario, data, horario_dia_hoje=None, horario_dia_ontem=None):
    """
    Marcações reais do dia + ajustes manuais, mesclados em ordem cronológica.
    Um ajuste com `marcacao_original` substitui a marcação real que corrige
    (a MarcacaoRelogio em si nunca é alterada/apagada, só sai da conta);
    sem `marcacao_original` é uma batida que faltou e vira um evento a mais.
    """
    inicio, fim = _janela_do_dia(data, horario_dia_hoje, horario_dia_ontem)
    reais = list(
        MarcacaoRelogio.objects
        .filter(funcionario=funcionario, momento__gte=inicio, momento__lt=fim)
        .order_by('momento')
    )
    ajustes = list(AjustePonto.objects.filter(funcionario=funcionario, data=data, status='aprovado'))
    ids_corrigidos = {a.marcacao_original_id for a in ajustes if a.marcacao_original_id}

    eventos = [_Evento(momento=m.momento, manual=False) for m in reais if m.id not in ids_corrigidos]
    for ajuste in ajustes:
        momento = timezone.make_aware(datetime.combine(data, ajuste.horario_ajustado))
        eventos.append(_Evento(momento=momento, manual=True))
    eventos.sort(key=lambda evento: evento.momento)
    return eventos


def _marcacoes_pareadas(funcionario, data, horario_dia_hoje=None, horario_dia_ontem=None):
    eventos = _eventos_do_dia(funcionario, data, horario_dia_hoje, horario_dia_ontem)

    segundos_trabalhados = 0
    pares = []
    for i in range(0, len(eventos) - 1, 2):
        segundos_trabalhados += int((eventos[i + 1].momento - eventos[i].momento).total_seconds())
        pares.append((eventos[i], eventos[i + 1]))

    marcacao_impar = len(eventos) % 2 == 1
    if marcacao_impar:
        pares.append((eventos[-1], None))  # última batida sem par, sinalizada mas visível

    return eventos, pares[:MAX_PARES_EXIBIDOS], segundos_trabalhados, marcacao_impar


def _taxa_extra_do_dia(data, em_12x36):
    if em_12x36:
        return 50
    if data.isoweekday() == 7 or Feriado.objects.filter(data=data).exists():
        return 100
    return 50


def _horario_dia_de(funcionario, data):
    jornada = FuncionarioJornada.vigente_em(funcionario, data)
    if jornada is None:
        return None
    horario_dia, _ = _horario_do_dia(jornada, data)
    return horario_dia


def calcular_periodo(funcionario, data_inicio, data_fim):
    dias = []
    dia_atual = data_inicio
    horario_dia_anterior = _horario_dia_de(funcionario, data_inicio - timedelta(days=1))
    dias_ferias = _dias_ferias_aprovadas(funcionario)
    dias_atestado = _dias_atestado(funcionario)

    while dia_atual <= data_fim:
        jornada = FuncionarioJornada.vigente_em(funcionario, dia_atual)
        em_ferias = dia_atual in dias_ferias
        em_atestado = not em_ferias and dia_atual in dias_atestado

        if jornada is None:
            # Sem jornada cadastrada não quer dizer que o funcionário não bateu
            # ponto -- ainda mostramos as batidas reais do dia (só não há
            # esperado nenhum pra comparar, então não entra no total de horas).
            marcacoes, pares, segundos_trabalhados, marcacao_impar = _marcacoes_pareadas(
                funcionario, dia_atual, None, horario_dia_anterior
            )
            dias.append({
                'data': dia_atual, 'sem_jornada': True, 'marcacoes': marcacoes, 'pares': pares,
                'entrada_esperada': None, 'saida_esperada': None,
                'segundos_esperados': 0, 'segundos_trabalhados': segundos_trabalhados, 'saldo_segundos': 0,
                'segundos_normais': 0, 'segundos_falta_dia': 0, 'segundos_extra_50_dia': 0, 'segundos_extra_100_dia': 0,
                'marcacao_impar': marcacao_impar, 'taxa_extra': None, 'folga': False,
                'ferias': em_ferias, 'atestado': em_atestado,
            })
            horario_dia_anterior = None
            dia_atual += timedelta(days=1)
            continue

        horario_dia, em_12x36 = _horario_do_dia(jornada, dia_atual)
        # Em férias aprovadas ou atestado, o dia não tem jornada esperada --
        # trata igual folga pro cálculo (0 esperado, sem desconto de falta),
        # só troca o rótulo exibido (ver `situacao_do_dia`/templates).
        if em_ferias or em_atestado:
            horario_dia = None
        segundos_esperados = _segundos_esperados(horario_dia)
        entrada_esperada = _primeira_entrada(horario_dia) if horario_dia else None
        saida_esperada = _ultima_saida(horario_dia) if horario_dia else None
        marcacoes, pares, segundos_trabalhados, marcacao_impar = _marcacoes_pareadas(
            funcionario, dia_atual, horario_dia, horario_dia_anterior
        )
        horario_dia_anterior = horario_dia
        saldo_segundos = segundos_trabalhados - segundos_esperados
        taxa_extra = _taxa_extra_do_dia(dia_atual, em_12x36)

        dias.append({
            'data': dia_atual,
            'sem_jornada': False,
            # `horario_dia is None` cobre o dia de folga do 12x36, férias e
            # atestado; numa jornada fixa, o HorarioDia do fim de semana
            # existe mas sem nenhum entrada/saída -- `.folga` (a property do
            # model) cobre esse caso.
            'folga': horario_dia is None or horario_dia.folga,
            'ferias': em_ferias,
            'atestado': em_atestado,
            'marcacoes': marcacoes,
            'pares': pares,
            'marcacao_impar': marcacao_impar,
            'entrada_esperada': entrada_esperada,
            'saida_esperada': saida_esperada,
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


def situacao_do_dia(dia, hoje):
    """
    Traduz um dia de `calcular_periodo` pra algo exibível ao colaborador:
    (texto, cor, pode_solicitar_correcao). `cor` reaproveita as classes
    `.badge-status--<cor>` já existentes no CSS (aprovado/pendente/rejeitado/
    inativo) -- não tem relação com o `status` do AjustePonto, é só a mesma
    paleta de cores.

    Hoje nunca aparece como pendência (o dia ainda não terminou) mesmo que já
    esteja com marcação ímpar ou saldo negativo -- só os dias já fechados são
    sinalizados pra correção.
    """
    if dia['ferias']:
        return 'Férias', 'inativo', False
    if dia['atestado']:
        return 'Atestado', 'inativo', False
    if dia['sem_jornada']:
        return 'Sem jornada', 'inativo', False
    if dia['folga']:
        return 'Folga', 'inativo', False

    if dia['marcacao_impar']:
        texto, cor = 'Faltou marcação', 'pendente'
    elif dia['segundos_trabalhados'] == 0:
        texto, cor = 'Sem batidas', 'rejeitado'
    elif dia['saldo_segundos'] < 0:
        texto, cor = 'Falta/atraso', 'pendente'
    else:
        return 'OK', 'aprovado', False

    if dia['data'] == hoje:
        return 'Em andamento', 'inativo', False

    return texto, cor, True
