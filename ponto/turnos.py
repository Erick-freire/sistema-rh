"""
Associação de batidas a TURNOS (e não a dias de calendário).

Por que existe: o motor de cálculo antigo buscava as batidas "do dia" (00:00 a
24:00). Num turno que atravessa a meia-noite (22:00 -> 06:00) a entrada cai num
dia e a saída no outro, e cada dia ficava com uma batida solta. Aqui o ponto de
partida é o TURNO previsto na escala, e as batidas são encaixadas nele.

Este módulo é PURO: não importa Django nem toca no banco. Recebe datetimes e
devolve estruturas simples. Isso permite testar sem banco (ver test_turnos.py).
Quem monta os turnos previstos a partir de HorarioDia é o calculos.py.

Como a janela de cada turno é definida:
  - ela vai do ponto médio entre o fim do turno anterior e o início deste, até o
    ponto médio entre o fim deste e o início do próximo;
  - mas nunca passa de MARGEM_MAXIMA antes do início nem depois do fim.
O limite existe por causa do 12x36: entre dois turnos há 36h, e sem o limite a
janela de um turno engoliria metade do dia de folga, escondendo um "dobrou o turno".

Batidas fora de qualquer janela (folga, ou funcionário sem escala) são agrupadas
por proximidade (INTERVALO_MAXIMO_ENTRE_BATIDAS) e viram um turno sem previsão.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import List, Optional

# Valores provisórios: mais adiante vão para a tela de parâmetros do RH.
MARGEM_MAXIMA = timedelta(hours=6)
# Precisa ser maior que um turno de 12h com só entrada e saída (12x36), senão ele seria
# cortado em dois grupos. Só vale para batidas FORA de escala; as de turno previsto
# não passam por esta regra.
INTERVALO_MAXIMO_ENTRE_BATIDAS = timedelta(hours=14)


@dataclass
class TurnoPrevisto:
    data: date          # dia em que o turno COMEÇA (é a chave da linha no espelho)
    inicio: datetime
    fim: datetime       # pode cair no dia seguinte


@dataclass
class TurnoApurado:
    data: date
    previsto: Optional[TurnoPrevisto]   # None = batidas fora de escala
    momentos: List[datetime] = field(default_factory=list)


def turno_previsto_do_dia(data: date, horas: List[time], tzinfo=None) -> Optional[TurnoPrevisto]:
    """
    `horas` = horários do HorarioDia em ordem (entrada1, saida1, entrada2, ...),
    já sem os campos vazios. Cada horário menor que o anterior significa que
    passou da meia-noite.
    """
    if not horas:
        return None
    atual = datetime.combine(data, horas[0], tzinfo=tzinfo)
    inicio = atual
    for hora in horas[1:]:
        candidato = datetime.combine(atual.date(), hora, tzinfo=tzinfo)
        if candidato < atual:
            candidato += timedelta(days=1)
        atual = candidato
    return TurnoPrevisto(data=data, inicio=inicio, fim=atual)


def _janelas(previstos, margem_maxima):
    janelas = []
    ultimo = len(previstos) - 1
    for i, turno in enumerate(previstos):
        if i > 0:
            anterior = previstos[i - 1]
            inicio_janela = anterior.fim + (turno.inicio - anterior.fim) / 2
        else:
            inicio_janela = turno.inicio - margem_maxima

        if i < ultimo:
            seguinte = previstos[i + 1]
            fim_janela = turno.fim + (seguinte.inicio - turno.fim) / 2
        else:
            fim_janela = turno.fim + margem_maxima

        inicio_janela = max(inicio_janela, turno.inicio - margem_maxima)
        fim_janela = min(fim_janela, turno.fim + margem_maxima)
        janelas.append((inicio_janela, fim_janela))
    return janelas


def _agrupar_sobras(sobras, intervalo_maximo, tzinfo):
    grupos = []
    for momento in sobras:
        if grupos and momento - grupos[-1][-1] <= intervalo_maximo:
            grupos[-1].append(momento)
        else:
            grupos.append([momento])

    apurados = []
    for grupo in grupos:
        primeiro = grupo[0].astimezone(tzinfo) if tzinfo else grupo[0]
        apurados.append(TurnoApurado(data=primeiro.date(), previsto=None, momentos=grupo))
    return apurados


def agrupar_batidas_em_turnos(
    turnos_previstos,
    momentos,
    margem_maxima=MARGEM_MAXIMA,
    intervalo_maximo=INTERVALO_MAXIMO_ENTRE_BATIDAS,
    tzinfo=None,
):
    """
    Devolve uma lista de TurnoApurado ordenada por data: um item para cada turno
    previsto (mesmo sem nenhuma batida, que é o caso de possível falta) e um
    item extra para cada grupo de batidas fora de escala.
    """
    previstos = sorted(turnos_previstos, key=lambda t: t.inicio)
    janelas = _janelas(previstos, margem_maxima)
    apurados = [TurnoApurado(data=t.data, previsto=t) for t in previstos]

    sobras = []
    for momento in sorted(momentos):
        for apurado, (inicio_janela, fim_janela) in zip(apurados, janelas):
            if inicio_janela <= momento < fim_janela:
                apurado.momentos.append(momento)
                break
        else:
            sobras.append(momento)

    apurados.extend(_agrupar_sobras(sobras, intervalo_maximo, tzinfo))
    apurados.sort(key=lambda a: (a.data, a.previsto is None))
    return apurados