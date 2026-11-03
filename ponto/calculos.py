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
- Tolerância diária, igual pros dois lados: falta/atraso ou hora extra de até
  15 minutos no dia (somando o dia todo) não conta -- o saldo do dia fica zero.
  É o saldo líquido: chegar 30 min atrasado e sair 20 min depois dá 10 min, que
  ficam na tolerância. Passou disso, conta inteiro: 20 minutos de atraso são 20
  minutos de falta, e 16 minutos a mais são 16 minutos de extra (não 5 nem 1).
  "Normais" continua sendo só o tempo realmente trabalhado. Só vale em dia com
  horário previsto: em folga, domingo de folga e feriado todo minuto trabalhado
  é extra.
- Compensação: dentro do período calculado, hora extra compensa falta/atraso
  primeiro pelas horas de 50%; só depois de esgotar essas é que consome as
  de 100%. Só o saldo depois de compensar vira "extra a pagar" ou "falta a
  descontar".
- Dia Falta: dia de trabalho já encerrado sem nenhuma batida. Não entra na
  compensação (hora extra não abate dia inteiro), desconta o DSR da semana
  (segunda a domingo; várias faltas na mesma semana perdem um DSR só) e tira a
  assiduidade do período. Atestado, férias, feriado e folga nunca são Dia Falta.
- Hora noturna (decisão do DP em 30/10/2026, igual ao Secullum): esperado,
  normais, saldo, faltas, extras e as horas noturnas do dia são todos no
  relógio, sem a redução da hora de 52min30s. A redução só aparece no total do
  período (`noturno_convertido_segundos`: 7h de relógio = 8h), que é a base do
  adicional noturno em R$ e o que vai para a folha. `segundos_noturnos` é o
  tempo entre 22:00 e 05:00 (ver `turnos.segundos_noturnos`).
- Valores em R$ (`valores_do_periodo`): ver as constantes lá.

Turno que atravessa a meia-noite (ex.: 12x36 noturno 18:00-06:00, horário
1003): a batida inteira fica no dia em que o turno COMEÇOU, nunca dividida
entre os dois dias -- é assim que o Secullum já trata (conferido direto no
sistema deles).

Como as batidas são encaixadas nos dias: em vez de buscar "as batidas do dia"
(00:00 a 24:00), o cálculo monta os turnos PREVISTOS do período e encaixa cada
batida no turno a que pertence (ver `turnos.agrupar_batidas_em_turnos`). Cada
turno tem uma janela com margem antes e depois, o que cobre entrada
antecipada e saída tardia, inclusive em turno noturno. Batida que não cai em
nenhum turno (folga, sem jornada) fica no dia em que foi batida. Batida do
relógio repetida no mesmo minuto conta uma vez só (ver `_sem_batidas_repetidas`).

Ajustes manuais aprovados entram como uma batida a mais, na data e hora
lançadas (`AjustePonto.data` + `horario_ajustado`, sem deduzir nada), e passam
pelo mesmo encaixe.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from django.db.models import Q
from django.utils import timezone

from .models import AjustePonto, Feriado, FuncionarioJornada, MarcacaoRelogio
from .turnos import agrupar_batidas_em_turnos, em_hora_noturna, segundos_noturnos, turno_previsto_do_dia
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


def _dias_feriado(data_inicio, data_fim):
    return {feriado.data for feriado in Feriado.objects.filter(data__gte=data_inicio, data__lte=data_fim)}

# date.isoweekday() (1=segunda...7=domingo) já bate exatamente com HorarioDia.dia_semana.


@dataclass
class _Evento:
    """
    Um ponto no tempo pra fim de pareamento -- tanto faz se veio de uma
    MarcacaoRelogio real ou de um AjustePonto manual, o pareamento é sempre
    por ordem cronológica. `manual` é só pra sinalizar o "*" na tela.
    `marcacao_id` só existe nas batidas reais: é como um ajuste que corrige uma
    marcação (`marcacao_original`) a tira da conta.
    """
    momento: object
    manual: bool
    marcacao_id: Optional[int] = None


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


def _segundos_noturnos_trabalhados(eventos):
    """Segundos de relógio entre 22:00 e 05:00 nos pares completos de batidas (mesmo pareamento de `_parear_eventos`)."""
    total = 0
    for i in range(0, len(eventos) - 1, 2):
        inicio, fim = timezone.localtime(eventos[i].momento), timezone.localtime(eventos[i + 1].momento)
        total += segundos_noturnos(inicio, fim)
    return total


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
    LEGADO: o cálculo não usa mais esta função (as batidas agora são encaixadas
    por `turnos.agrupar_batidas_em_turnos`, que também cobre entrada antecipada
    em turno noturno). Foi mantida só porque testes antigos podem referenciá-la;
    pode ser removida quando eles forem migrados.

    (início, fim) aware que era usada pra buscar as marcações do dia `data`.
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

# Vale para falta/atraso e para hora extra. Valor provisório: mais adiante vai
# para a tela de parâmetros do RH (igual às margens de `turnos.py`). Inclusivo:
# 15 minutos exatos ainda ficam na tolerância.
TOLERANCIA_DIARIA_SEGUNDOS = 15 * 60


def _horarios_em_ordem(horario_dia):
    """Os horários do HorarioDia na ordem em que acontecem (entrada1, saída1, entrada2...), sem os vazios."""
    if horario_dia is None:
        return []
    campos = (
        horario_dia.entrada1, horario_dia.saida1,
        horario_dia.entrada2, horario_dia.saida2,
        horario_dia.entrada3, horario_dia.saida3,
    )
    return [campo for campo in campos if campo]


def _info_dos_dias(funcionario, data_inicio, data_fim, dias_ferias, dias_atestado, dias_feriado=frozenset()):
    """
    Para cada dia de `data_inicio` a `data_fim`: jornada vigente, HorarioDia efetivo
    (None em folga, férias, atestado ou feriado) e as flags de férias/atestado/feriado.

    Feriado: se o dia está cadastrado como feriado, o horário tem `considerar_feriados`
    e a jornada NÃO é 12x36, o dia vira folga (esperado zero, sem falta); quem trabalha
    nele recebe extra de 100%. No 12x36 o feriado é dia normal de trabalho. O intervalo
    pedido vai além do período calculado (ver `calcular_periodo`), porque para
    encaixar as batidas é preciso saber onde termina o turno anterior e onde
    começa o seguinte.
    """
    info = {}
    dia = data_inicio
    while dia <= data_fim:
        jornada = FuncionarioJornada.vigente_em(funcionario, dia)
        em_ferias = dia in dias_ferias
        em_atestado = not em_ferias and dia in dias_atestado

        horario_dia, em_12x36 = None, False
        em_feriado = False
        if jornada is not None:
            horario_dia, em_12x36 = _horario_do_dia(jornada, dia)
            em_feriado = (
                dia in dias_feriado and not em_12x36 and bool(jornada.horario.considerar_feriados)
            )
            # Em férias aprovadas, atestado ou feriado, o dia não tem jornada esperada --
            # trata igual folga pro cálculo (0 esperado, sem desconto de falta),
            # só troca o rótulo exibido (ver `situacao_do_dia`/templates).
            if em_ferias or em_atestado or em_feriado:
                horario_dia = None

        info[dia] = {
            'jornada': jornada,
            'horario_dia': horario_dia,
            'em_12x36': em_12x36,
            'em_ferias': em_ferias,
            'em_atestado': em_atestado,
            'em_feriado': em_feriado,
        }
        dia += timedelta(days=1)
    return info


def _sem_batidas_repetidas(marcacoes):
    """
    Tira da conta a batida do relógio repetida no mesmo minuto da anterior: a
    pessoa bateu duas vezes e o relógio gravou as duas (NSRs seguidos). Fica a
    primeira; a repetida continua gravada, só não entra no cálculo. Sem isso, uma
    batida a mais desloca todos os pares do dia (o almoço vira tempo trabalhado).

    Só o mesmo minuto (confirmado com o usuário): batida 1 minuto depois já conta.
    Precisa rodar ANTES de tirar as batidas corrigidas por ajuste -- se o RH
    corrigir a batida que ficou, a repetida não pode voltar no lugar dela.
    `marcacoes` em ordem cronológica.
    """
    ultimo_minuto = None
    for marcacao in marcacoes:
        minuto = marcacao.momento.replace(second=0, microsecond=0)
        if minuto != ultimo_minuto:
            yield marcacao
        ultimo_minuto = minuto


def _eventos_por_data(funcionario, data_inicio, data_fim, info=None):
    """
    {dia: [eventos em ordem cronológica]} para cada dia de `data_inicio` a `data_fim`.

    Marcações reais + ajustes manuais aprovados, todos encaixados juntos no turno
    a que pertencem. Um ajuste com `marcacao_original` substitui a marcação real
    que corrige (a MarcacaoRelogio em si nunca é alterada/apagada, só sai da
    conta); sem `marcacao_original` é uma batida que faltou e vira um evento a
    mais. Nos dois casos o ajuste vale na data e hora em que foi lançado: quem
    lança (RH ou colaborador) informa o dia real da batida, inclusive depois da
    meia-noite.
    """
    if info is None:
        inicio_info, fim_info = data_inicio - timedelta(days=2), data_fim + timedelta(days=2)
        info = _info_dos_dias(
            funcionario, inicio_info, fim_info,
            _dias_ferias_aprovadas(funcionario), _dias_atestado(funcionario),
            _dias_feriado(inicio_info, fim_info),
        )
    tz = timezone.get_current_timezone()

    previstos = []
    for dia, dados in info.items():
        turno = turno_previsto_do_dia(dia, _horarios_em_ordem(dados['horario_dia']), tzinfo=tz)
        if turno is not None:
            previstos.append(turno)

    inicio_busca = intervalo_do_dia(data_inicio - timedelta(days=1))[0]
    fim_busca = intervalo_do_dia(data_fim + timedelta(days=1))[1]

    # Mesma janela das batidas reais. A correção também vem pela batida original,
    # pra ela sair da conta mesmo que a data lançada caia fora da janela.
    ajustes = list(
        AjustePonto.objects
        .filter(funcionario=funcionario, status='aprovado')
        .filter(
            Q(data__gte=data_inicio - timedelta(days=1), data__lte=data_fim + timedelta(days=1))
            | Q(marcacao_original__momento__gte=inicio_busca, marcacao_original__momento__lt=fim_busca)
        )
    )
    ids_corrigidos = {a.marcacao_original_id for a in ajustes if a.marcacao_original_id}

    reais = (
        MarcacaoRelogio.objects
        .filter(funcionario=funcionario, momento__gte=inicio_busca, momento__lt=fim_busca)
        .order_by('momento', 'id')
    )
    eventos = [
        _Evento(momento=m.momento, manual=False, marcacao_id=m.id)
        for m in _sem_batidas_repetidas(reais)
        if m.id not in ids_corrigidos
    ]
    eventos.extend(
        _Evento(momento=timezone.make_aware(datetime.combine(a.data, a.horario_ajustado)), manual=True)
        for a in ajustes
    )

    resultado = {}
    dia = data_inicio
    while dia <= data_fim:
        resultado[dia] = []
        dia += timedelta(days=1)

    for turno in agrupar_batidas_em_turnos(previstos, eventos, tzinfo=tz, chave=lambda e: e.momento):
        if turno.data in resultado:
            resultado[turno.data].extend(turno.momentos)
    for eventos_do_dia in resultado.values():
        eventos_do_dia.sort(key=lambda evento: evento.momento)
    return resultado


def _eventos_do_dia(funcionario, data, horario_dia_hoje=None, horario_dia_ontem=None):
    """
    Eventos de um único dia. Os parâmetros de horário não são mais usados (o
    encaixe nos turnos olha os dias vizinhos sozinho); ficam só para não quebrar
    quem ainda chama com eles.
    """
    return _eventos_por_data(funcionario, data, data)[data]


def _parear_eventos(eventos):
    segundos_trabalhados = 0
    pares = []
    for i in range(0, len(eventos) - 1, 2):
        segundos_trabalhados += int((eventos[i + 1].momento - eventos[i].momento).total_seconds())
        pares.append((eventos[i], eventos[i + 1]))

    marcacao_impar = len(eventos) % 2 == 1
    if marcacao_impar:
        pares.append((eventos[-1], None))  # última batida sem par, sinalizada mas visível

    return eventos, pares[:MAX_PARES_EXIBIDOS], segundos_trabalhados, marcacao_impar


def _marcacoes_pareadas(funcionario, data, horario_dia_hoje=None, horario_dia_ontem=None):
    return _parear_eventos(_eventos_do_dia(funcionario, data, horario_dia_hoje, horario_dia_ontem))


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
    hoje = timezone.localdate()
    dias_ferias = _dias_ferias_aprovadas(funcionario)
    dias_atestado = _dias_atestado(funcionario)

    # Dois dias a mais de cada lado: o turno que começou antes do período pode
    # terminar dentro dele, e o último turno do período pode terminar depois.
    inicio_info, fim_info = data_inicio - timedelta(days=2), data_fim + timedelta(days=2)
    info = _info_dos_dias(
        funcionario, inicio_info, fim_info,
        dias_ferias, dias_atestado, _dias_feriado(inicio_info, fim_info),
    )
    eventos_por_data = _eventos_por_data(funcionario, data_inicio, data_fim, info)

    dia_atual = data_inicio
    while dia_atual <= data_fim:
        dados = info[dia_atual]
        jornada = dados['jornada']
        em_ferias = dados['em_ferias']
        em_atestado = dados['em_atestado']
        marcacoes, pares, segundos_trabalhados, marcacao_impar = _parear_eventos(eventos_por_data[dia_atual])
        noturnos_trabalhados = _segundos_noturnos_trabalhados(marcacoes)

        if jornada is None:
            # Sem jornada cadastrada não quer dizer que o funcionário não bateu
            # ponto -- ainda mostramos as batidas reais do dia (só não há
            # esperado nenhum pra comparar, então não entra no total de horas).
            dias.append({
                'data': dia_atual, 'sem_jornada': True, 'marcacoes': marcacoes, 'pares': pares,
                'entrada_esperada': None, 'saida_esperada': None,
                'segundos_esperados': 0, 'segundos_trabalhados': segundos_trabalhados, 'saldo_segundos': 0,
                'segundos_normais': 0, 'segundos_falta_dia': 0, 'segundos_extra_50_dia': 0, 'segundos_extra_100_dia': 0,
                'segundos_noturnos': noturnos_trabalhados,
                'marcacao_impar': marcacao_impar, 'taxa_extra': None, 'folga': False,
                'ferias': em_ferias, 'atestado': em_atestado, 'feriado': False, 'dia_falta': False,
            })
            dia_atual += timedelta(days=1)
            continue

        horario_dia = dados['horario_dia']
        em_12x36 = dados['em_12x36']
        segundos_esperados = _segundos_esperados(horario_dia)
        entrada_esperada = _primeira_entrada(horario_dia) if horario_dia else None
        saida_esperada = _ultima_saida(horario_dia) if horario_dia else None
        saldo_segundos = segundos_trabalhados - segundos_esperados
        # Dentro da tolerância (pra menos ou pra mais) o dia fecha zerado: não
        # aparece como falta nem como extra, não entra na compensação e não vira
        # "Falta/atraso" na tela do colaborador. `segundos_normais` não muda.
        # Sem horário previsto (folga, feriado, férias, atestado) não há horário
        # pra variar: todo minuto trabalhado conta como extra.
        if segundos_esperados and abs(saldo_segundos) <= TOLERANCIA_DIARIA_SEGUNDOS:
            saldo_segundos = 0
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
            'feriado': dados['em_feriado'],
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
            # Todo o trabalho entre 22:00 e 05:00, normal ou extra, no relógio.
            'segundos_noturnos': noturnos_trabalhados,
            'segundos_falta_dia': max(0, -saldo_segundos),
            'segundos_extra_50_dia': max(0, saldo_segundos) if taxa_extra == 50 else 0,
            'segundos_extra_100_dia': max(0, saldo_segundos) if taxa_extra == 100 else 0,
            'taxa_extra': taxa_extra,
            # Hoje ainda pode chegar batida: só dia já encerrado vira Dia Falta.
            'dia_falta': bool(segundos_esperados) and not marcacoes and dia_atual < hoje,
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
    # Dia Falta fica fora da compensação: é descontado como dia, não como horas.
    deficit = -sum(d['saldo_segundos'] for d in dias if d['saldo_segundos'] < 0 and not d['dia_falta'])

    consumo_50 = min(deficit, superavit_50)
    deficit_restante = deficit - consumo_50
    consumo_100 = min(deficit_restante, superavit_100)

    dias_falta = [d['data'] for d in dias if d['dia_falta']]
    # Dia sem jornada fica de fora, como no resto dos totais (ele é sinalizado à parte).
    noturno_segundos = sum(d['segundos_noturnos'] for d in dias if not d['sem_jornada'])
    totais = {
        'extra_50_segundos': superavit_50 - consumo_50,
        'extra_100_segundos': superavit_100 - consumo_100,
        'falta_segundos': deficit_restante - consumo_100,
        'noturno_segundos': noturno_segundos,
        # Na hora de 52min30s: base do adicional noturno em R$ e da coluna da folha.
        'noturno_convertido_segundos': em_hora_noturna(noturno_segundos),
        'dias_falta': len(dias_falta),
        'dsr_perdidos': len({dia.isocalendar()[:2] for dia in dias_falta}),  # um por semana (ano, semana ISO)
        'perdeu_assiduidade': bool(dias_falta),
        'dias_sem_jornada': sum(1 for d in dias if d['sem_jornada']),
        'dias_com_marcacao_impar': sum(1 for d in dias if d['marcacao_impar']),
    }

    return {'dias': dias, 'totais': totais, 'pares_no_periodo': pares_no_periodo}


# Provisórios -- confirmados com o usuário, ainda a validar com o DP: o divisor
# 220 vale também para meio período, e o Dia Falta e o DSR perdido valem 1/30
# do salário cada.
DIVISOR_SALARIO_HORA = 220
DIAS_DO_MES = 30
# Confirmado com o usuário: 20% do valor da hora, por hora noturna convertida.
ADICIONAL_NOTURNO = Decimal('0.20')


def valores_do_periodo(totais, salario):
    """
    Os totais de `calcular_periodo` em R$, a partir do salário base; None sem salário
    cadastrado. Hora: salário / 220. Extra 50%: hora x 1,5 x horas; extra 100%: hora
    x 2 x horas; falta/atraso: hora x horas; adicional noturno: hora x 20% x horas
    noturnas convertidas (na hora de 52min30s). Cada linha é arredondada no centavo.
    """
    if salario is None:
        return None
    valor_hora = Decimal(salario) / DIVISOR_SALARIO_HORA
    valor_dia = Decimal(salario) / DIAS_DO_MES

    def horas(segundos):
        return Decimal(segundos) / 3600

    valores = {
        'extra_50': valor_hora * Decimal('1.5') * horas(totais['extra_50_segundos']),
        'extra_100': valor_hora * 2 * horas(totais['extra_100_segundos']),
        'adicional_noturno': valor_hora * ADICIONAL_NOTURNO * horas(totais['noturno_convertido_segundos']),
        'falta': valor_hora * horas(totais['falta_segundos']),
        'dias_falta': valor_dia * totais['dias_falta'],
        'dsr': valor_dia * totais['dsr_perdidos'],
    }
    valores = {chave: valor.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) for chave, valor in valores.items()}
    valores['valor_hora'] = valor_hora.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    valores['a_pagar'] = valores['extra_50'] + valores['extra_100'] + valores['adicional_noturno']
    valores['a_descontar'] = valores['falta'] + valores['dias_falta'] + valores['dsr']
    return valores


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
    if dia.get('feriado'):
        return 'Feriado', 'inativo', False
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


# CLT art. 71: até 4h trabalhadas no dia não exige intervalo; acima de 4h até
# 6h, 15 min; acima de 6h (12x36 inclusive), 1h.
JORNADA_QUE_EXIGE_15_MIN_SEGUNDOS = 4 * 3600
JORNADA_QUE_EXIGE_1H_SEGUNDOS = 6 * 3600


def intervalo_minimo(segundos_trabalhados):
    """Intervalo mínimo exigido, em segundos, para o tempo trabalhado no dia (0 = nenhum)."""
    if segundos_trabalhados > JORNADA_QUE_EXIGE_1H_SEGUNDOS:
        return 60 * 60
    if segundos_trabalhados > JORNADA_QUE_EXIGE_15_MIN_SEGUNDOS:
        return 15 * 60
    return 0


def intervalo_curto(dia):
    """
    (saída, volta) do maior intervalo de um dia de `calcular_periodo`, se ele ficou
    abaixo do mínimo (`intervalo_minimo`); senão None. Quem usa: o aviso diário ao
    RH e ao gestor (`manage.py avisar_intervalos_curtos`).

    Vale o MAIOR intervalo entre os pares de batidas do dia -- assim o café de
    20 min não é confundido com o almoço. Fica de fora (decisões do usuário):
    - dia com até 4h trabalhadas (não exige intervalo);
    - marcação ímpar (não dá pra saber o que é saída e o que é volta);
    - dia sem nenhum intervalo batido (pode ser só esquecimento de bater).
    Sem tolerância: 59 minutos já é curto num dia de mais de 6h.
    """
    minimo = intervalo_minimo(dia['segundos_trabalhados'])
    if dia['marcacao_impar'] or not minimo:
        return None
    eventos = dia['marcacoes']
    intervalos = [(eventos[i], eventos[i + 1]) for i in range(1, len(eventos) - 1, 2)]
    if not intervalos:
        return None
    saida, volta = max(intervalos, key=lambda par: par[1].momento - par[0].momento)
    if (volta.momento - saida.momento).total_seconds() >= minimo:
        return None
    return saida, volta