"""
Edição em lote do espelho de ponto (tela Espelho e cálculo): cada Ent./Saí.
vira uma caixa de texto e um "Salvar" grava todas as batidas alteradas de uma
vez. Por baixo, é o mesmo lançamento do lápis (`views.ajustar_marcacao_view`):
nunca mexe na MarcacaoRelogio; cria AjustePonto já aprovado.

Cada caixa vem com a origem da batida que mostrava (`m<id>` = batida do
relógio, `a<id>` = ajuste manual, vazio = sem batida). Ao salvar, o espelho é
recalculado e a origem precisa bater: se alguém mexeu no ponto no meio do
caminho, nada é gravado (senão a correção cairia na batida errada).

O que cada alteração faz:
- caixa vazia preenchida: batida que faltou (ajuste sem `marcacao_original`);
- batida do relógio com outro horário: correção (`marcacao_original` = ela);
- ajuste manual com outro horário: o ajuste antigo fica "rejeitado" com a
  observação de que foi substituído e entra um novo no lugar (nada é apagado);
- ajuste manual apagado: fica "rejeitado" ("removido no espelho"); se ele
  corrigia uma batida do relógio, ela volta a valer;
- batida do relógio apagada: recusado -- não há como desconsiderar batida do
  relógio, só corrigir o horário.

No espelho do setor (um dia, várias pessoas) os nomes das caixas ganham o
prefixo da pessoa (`prefixo_do_funcionario`), e todo mundo é conferido antes
de gravar qualquer um (`conferir_batidas` + `gravar_batidas`).

Data da batida: a mesma da batida que estava na caixa (ou o dia da linha, na
caixa vazia). Se o horário digitado ficar antes da batida anterior da linha e
o dia seguinte couber num turno (`DURACAO_MAXIMA_TURNO`), vai para o dia
seguinte -- é a saída depois da meia-noite (18:00 -> 06:00). Senão, é erro de
ordem. Com qualquer erro, nada é gravado.
"""
import re
from datetime import datetime, time, timedelta

from django.db import transaction
from django.utils import timezone

from .calculos import MAX_PARES_EXIBIDOS, calcular_periodo
from .forms import MENSAGEM_PERIODO_LONGO, periodo_longo_demais
from .models import AjustePonto
from .turnos import DURACAO_MAXIMA_TURNO

CELULAS_POR_DIA = 2 * MAX_PARES_EXIBIDOS
MOTIVO_PADRAO = 'Correção no espelho de ponto'
_CAMPO = re.compile(r'^b_(\d{8})_(\d+)$')
_HORARIO = re.compile(r'^(\d{1,2}):?(\d{2})$')


def nome_do_campo(data, indice):
    return f'b_{data:%Y%m%d}_{indice}'


def nome_da_origem(data, indice):
    return f'o_{data:%Y%m%d}_{indice}'


def prefixo_do_funcionario(funcionario):
    return f'f{funcionario.id}_'


def _sem_prefixo(dados, prefixo):
    if not prefixo:
        return dados
    return {chave[len(prefixo):]: valor for chave, valor in dados.items() if chave.startswith(prefixo)}


def codigo_da_origem(evento):
    if evento is None:
        return ''
    if evento.marcacao_id:
        return f'm{evento.marcacao_id}'
    if evento.ajuste_id:
        return f'a{evento.ajuste_id}'
    return ''


def _texto(evento):
    return timezone.localtime(evento.momento).strftime('%H:%M') if evento else ''


def ler_horario(texto):
    """'8:05', '08:05', '805' ou '0805' -> time(8, 5); None se não for um horário."""
    casamento = _HORARIO.match((texto or '').strip())
    if not casamento:
        return None
    hora, minuto = int(casamento[1]), int(casamento[2])
    if hora > 23 or minuto > 59:
        return None
    return time(hora, minuto)


def _normalizado(texto):
    horario = ler_horario(texto)
    return horario.strftime('%H:%M') if horario else (texto or '').strip()


def eventos_das_celulas(dia):
    """As 6 caixas da linha (Ent.1..Saí.3), na ordem; None onde não há batida."""
    eventos = [evento for par in dia['pares'][:MAX_PARES_EXIBIDOS] for evento in par]
    return (eventos + [None] * CELULAS_POR_DIA)[:CELULAS_POR_DIA]


def celulas_do_dia(dia, digitados=None, prefixo=''):
    """O que o template precisa de cada caixa. `digitados`: valores de um envio com erro, para não perdê-los."""
    digitados = digitados or {}
    celulas = []
    for indice, evento in enumerate(eventos_das_celulas(dia)):
        nome = prefixo + nome_do_campo(dia['data'], indice)
        original = _texto(evento)
        celulas.append({
            'evento': evento,
            'tipo': 'entrada' if indice % 2 == 0 else 'saida',
            'nome': nome,
            'nome_origem': prefixo + nome_da_origem(dia['data'], indice),
            'origem': codigo_da_origem(evento),
            'original': original,
            'valor': digitados.get(nome, original),
        })
    return celulas


def _conferir_dia(dia, dados):
    """(erros, operações) de uma linha do espelho."""
    data = dia['data']
    rotulo = f'{data:%d/%m}'
    erros, operacoes = [], []
    anterior = None
    for indice, evento in enumerate(eventos_das_celulas(dia)):
        nome = nome_do_campo(data, indice)
        original = _texto(evento)
        if nome in dados:
            if dados.get(nome_da_origem(data, indice), '') != codigo_da_origem(evento):
                return [
                    f'{rotulo}: as batidas deste dia mudaram depois que a tela foi aberta. '
                    'Recarregue o espelho e confira antes de salvar de novo.'
                ], []
            valor = _normalizado(dados.get(nome))
        else:
            valor = original

        if valor == original:
            if evento is not None:
                momento = timezone.localtime(evento.momento)
                if anterior is not None and momento <= anterior:
                    erros.append(f'{rotulo}: a batida das {original} fica antes da anterior ({anterior:%H:%M}). Confira a ordem.')
                anterior = momento
            continue

        if not valor:
            if evento.marcacao_id:
                erros.append(
                    f'{rotulo}: a batida do relógio das {original} não pode ser apagada. Digite o horário correto no lugar.'
                )
            else:
                operacoes.append(('remover', evento, None, None))
            continue

        horario = ler_horario(valor)
        if horario is None:
            erros.append(f'{rotulo}: "{valor}" não é um horário. Use HH:MM, por exemplo 08:05.')
            continue
        base = timezone.localtime(evento.momento).date() if evento else data
        momento = timezone.make_aware(datetime.combine(base, horario))
        if anterior is not None and momento <= anterior:
            dia_seguinte = momento + timedelta(days=1)
            if dia_seguinte - anterior > DURACAO_MAXIMA_TURNO:
                erros.append(f'{rotulo}: {valor} fica antes da batida anterior ({anterior:%H:%M}). Confira a ordem.')
                continue
            momento = dia_seguinte  # saída depois da meia-noite
        anterior = momento
        operacoes.append(('lancar', evento, 'entrada' if indice % 2 == 0 else 'saida', momento))
    return erros, operacoes


def _datas_do_formulario(dados):
    datas = set()
    for chave in dados:
        casamento = _CAMPO.match(chave)
        if casamento:
            try:
                datas.add(datetime.strptime(casamento[1], '%Y%m%d').date())
            except ValueError:
                pass
    return datas


def conferir_batidas(funcionario, dados, prefixo=''):
    """(operações, erros) das caixas de `funcionario` no POST `dados`, sem gravar nada."""
    dados = _sem_prefixo(dados, prefixo)
    datas = _datas_do_formulario(dados)
    if not datas:
        return [], []
    if periodo_longo_demais(min(datas), max(datas)):
        return [], [MENSAGEM_PERIODO_LONGO]

    erros, operacoes = [], []
    for dia in calcular_periodo(funcionario, min(datas), max(datas))['dias']:
        if dia['data'] in datas:
            erros_do_dia, operacoes_do_dia = _conferir_dia(dia, dados)
            erros += erros_do_dia
            operacoes += operacoes_do_dia
    return operacoes, erros


def salvar_batidas(funcionario, dados, usuario, motivo=''):
    """
    Confere e grava as caixas alteradas do espelho. `dados` é o POST. Devolve
    (quantas alterações, erros); com qualquer erro, nada é gravado.
    """
    operacoes, erros = conferir_batidas(funcionario, dados)
    if erros:
        return 0, erros
    with transaction.atomic():
        gravar_batidas(funcionario, operacoes, usuario, motivo)
    return len(operacoes), []


def gravar_batidas(funcionario, operacoes, usuario, motivo=''):
    """Grava as operações de `conferir_batidas`. Chame dentro de uma transação."""
    motivo = (motivo or '').strip() or MOTIVO_PADRAO
    agora = timezone.now()
    for acao, evento, tipo, momento in operacoes:
        marcacao_original_id = evento.marcacao_id if evento else None
        if evento is not None and evento.ajuste_id:
            antigo = AjustePonto.objects.select_for_update().get(id=evento.ajuste_id, funcionario=funcionario)
            marcacao_original_id = antigo.marcacao_original_id
            antigo.status = 'rejeitado'
            antigo.observacao_rh = (
                'Removido no espelho de ponto.' if acao == 'remover'
                else f'Substituído no espelho de ponto por {timezone.localtime(momento):%d/%m/%Y %H:%M}.'
            )
            antigo.analisado_por = usuario
            antigo.analisado_em = agora
            antigo.save(update_fields=['status', 'observacao_rh', 'analisado_por', 'analisado_em'])
        if acao == 'lancar':
            local = timezone.localtime(momento)
            AjustePonto.objects.create(
                funcionario=funcionario, data=local.date(), tipo_evento=tipo,
                horario_ajustado=local.time().replace(second=0, microsecond=0),
                marcacao_original_id=marcacao_original_id, motivo=motivo,
                # Lançado direto por quem corrige o ponto: já sai aprovado (igual ao lápis).
                usuario=usuario, status='aprovado', solicitado_por=usuario,
                analisado_por=usuario, analisado_em=agora,
            )
