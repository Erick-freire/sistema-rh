"""
Abrir, recalcular e fechar o fechamento de folha.

Competência = mês fechado, do dia 1 ao último dia (confirmado com o usuário
em 23/10/2026). As colunas do ponto saem de `ponto.calculos.calcular_periodo`
para esse intervalo; programa, incentivo e safra, das regras de prêmios; VT e
periculosidade vêm copiados do fechamento anterior; o resto começa em branco
(ver `models.CAMPOS_COPIADOS`).
"""
import calendar
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from funcionarios.models import Funcionario
from funcionarios.utils import formatar_moeda
from ponto.calculos import calcular_periodo

from .colunas import SEIS_PORCENTO
from .models import CAMPOS_COPIADOS, Fechamento, LinhaFechamento
from .premios import RegrasDoMes


def periodo_da_competencia(competencia):
    inicio = competencia.replace(day=1)
    return inicio, inicio.replace(day=calendar.monthrange(inicio.year, inicio.month)[1])


def funcionarios_da_folha():
    """Quem entra num fechamento novo: os ativos, sem os gestores placeholder da importação."""
    return (
        Funcionario.objects
        .filter(status='ativo')
        .exclude(matricula__startswith='PENDENTE-')
        .order_by('nome')
    )


def _minutos(segundos):
    return int((segundos + 30) // 60)  # arredonda no minuto (o noturno, em hora de 52min30s, tem segundos quebrados)


def _dias_de_atestado(funcionario, inicio, fim):
    dias = set()
    for atestado in funcionario.atestados.filter(data_inicio__lte=fim, data_fim__gte=inicio):
        dia = max(atestado.data_inicio, inicio)
        while dia <= min(atestado.data_fim, fim):
            dias.add(dia)
            dia += timedelta(days=1)
    return len(dias)


def calcular_ponto(funcionario, competencia):
    """
    As colunas do ponto de um funcionário na competência, mais os alertas do
    cálculo. Atestados = dias corridos de atestado dentro do mês. Assiduidade
    é a do cálculo (perde com Dia Falta) -- o RH ajusta quando precisar.
    """
    inicio, fim = periodo_da_competencia(competencia)
    totais = calcular_periodo(funcionario, inicio, fim)['totais']
    return {
        'valores': {
            'adicional_noturno_minutos': _minutos(totais['noturno_segundos']),
            'he_50_minutos': _minutos(totais['extra_50_segundos']),
            'he_100_minutos': _minutos(totais['extra_100_segundos']),
            'faltas_minutos': _minutos(totais['falta_segundos']),
            'faltas_dias': totais['dias_falta'],
            'atestados_dias': _dias_de_atestado(funcionario, inicio, fim),
            'assiduidade': not totais['perdeu_assiduidade'],
        },
        'dias_sem_jornada': totais['dias_sem_jornada'],
        'dias_com_marcacao_impar': totais['dias_com_marcacao_impar'],
        'calculado_em': timezone.now().isoformat(),
    }


def _atualizar_automaticos(linha, novos):
    """
    Grava valores automáticos (ponto ou regras) sem desfazer ajustes do RH: um
    campo só muda se a linha nunca foi preenchida automaticamente ou se ele
    ainda está igual ao último automático. Linha importada da planilha (sem
    automáticos) fica como veio. Chamar ANTES de trocar o JSON de referência.
    """
    for campo, valor in novos.items():
        anteriores = linha.valores_automaticos(campo)
        if anteriores is None or (campo in anteriores and not linha.editado_pelo_rh(campo)):
            setattr(linha, campo, valor)


def aplicar_ponto(linha, calculo):
    _atualizar_automaticos(linha, calculo['valores'])
    linha.ponto_calculado = calculo


def aplicar_regras(linha, regras):
    """Programa, incentivo e safra pelas regras de prêmios (`regras` = `premios.RegrasDoMes` da competência)."""
    valores = regras.valores_para(linha.funcionario)
    _atualizar_automaticos(linha, valores)
    linha.premios_aplicados = {
        'valores': {campo: None if valor is None else str(valor) for campo, valor in valores.items()},
        'aplicado_em': timezone.now().isoformat(),
    }


def atualizar_cadastro(linha):
    funcionario = linha.funcionario
    linha.codigo_empresa = funcionario.codigo_empresa
    linha.matricula = funcionario.matricula or ''
    linha.nome = ' '.join(funcionario.nome.split())  # há nomes com quebra de linha no cadastro


def nao_usa_vale_transporte(funcionario):
    """Respondeu em Meu Perfil que não usa. Quem ainda não respondeu (None) segue como o RH lançar."""
    return funcionario.usa_vale_transporte is False


def aplicar_opcao_vale_transporte(linha):
    """Quem não usa vale-transporte não tem desconto: a célula fica vazia."""
    if nao_usa_vale_transporte(linha.funcionario):
        linha.vt_seis_porcento, linha.vt_valor = False, None


def nova_linha(fechamento, funcionario, linha_anterior=None, calcular=True, regras=None):
    """
    Linha nova: VT e periculosidade copiados de `linha_anterior`, prêmios pelas
    regras e o resto em branco. Sem `calcular`, o ponto fica pendente.
    """
    linha = LinhaFechamento(fechamento=fechamento, funcionario=funcionario)
    atualizar_cadastro(linha)
    if linha_anterior is not None:
        for campo in CAMPOS_COPIADOS:
            setattr(linha, campo, getattr(linha_anterior, campo))
    aplicar_opcao_vale_transporte(linha)
    aplicar_regras(linha, regras or RegrasDoMes(fechamento.competencia))
    if calcular:
        aplicar_ponto(linha, calcular_ponto(funcionario, fechamento.competencia))
        linha.ponto_pendente = False
    linha.save()
    return linha


def fechamento_anterior(competencia):
    return Fechamento.objects.filter(competencia__lt=competencia).order_by('-competencia').first()


def linha_anterior(fechamento, funcionario):
    anterior = fechamento_anterior(fechamento.competencia)
    if anterior is None:
        return None
    return anterior.linhas.filter(funcionario=funcionario).first()


@transaction.atomic
def abrir_fechamento(competencia, usuario):
    """
    Cria o fechamento com todos os ativos, com o ponto pendente: o cálculo leva
    ~0,2 s por pessoa e é feito depois, em lotes (`calcular_pendentes`).
    """
    competencia = competencia.replace(day=1)
    fechamento = Fechamento.objects.create(competencia=competencia, criado_por=usuario)
    anterior = fechamento_anterior(competencia)
    linhas_anteriores = {linha.funcionario_id: linha for linha in anterior.linhas.all()} if anterior else {}
    regras = RegrasDoMes(competencia)
    for funcionario in funcionarios_da_folha():
        nova_linha(fechamento, funcionario, linhas_anteriores.get(funcionario.id), calcular=False, regras=regras)
    return fechamento


def recalcular_linha(linha, regras=None):
    """Atualiza do cadastro (inclusive a opção de VT, se mudou), reaplica as regras de prêmios e recalcula o ponto."""
    atualizar_cadastro(linha)
    aplicar_opcao_vale_transporte(linha)
    aplicar_regras(linha, regras or RegrasDoMes(linha.fechamento.competencia))
    aplicar_ponto(linha, calcular_ponto(linha.funcionario, linha.fechamento.competencia))
    linha.ponto_pendente = False
    linha.save()


# ~0,2 s por pessoa: um lote fica em poucos segundos por requisição.
LOTE_DE_CALCULO = 20


def calcular_pendentes(fechamento, limite=LOTE_DE_CALCULO):
    """Calcula o ponto de até `limite` linhas pendentes. Devolve quantas ainda faltam."""
    regras = RegrasDoMes(fechamento.competencia)
    for linha in fechamento.linhas_pendentes().select_related('funcionario', 'fechamento')[:limite]:
        recalcular_linha(linha, regras)
    return fechamento.linhas_pendentes().count()


def marcar_para_recalcular(fechamento):
    """O recálculo de todos também vai em lotes; os ajustes do RH continuam valendo (`_atualizar_automaticos`)."""
    fechamento.linhas.update(ponto_pendente=True)


def recalcular_fechamento(fechamento):
    """Tudo de uma vez, sem lotes: para testes e uso fora das telas."""
    marcar_para_recalcular(fechamento)
    while calcular_pendentes(fechamento):
        pass


def fechar(fechamento, usuario):
    fechamento.status = 'fechado'
    fechamento.fechado_por = usuario
    fechamento.fechado_em = timezone.now()
    fechamento.save(update_fields=['status', 'fechado_por', 'fechado_em'])


def reabrir(fechamento):
    fechamento.status = 'aberto'
    fechamento.fechado_por = None
    fechamento.fechado_em = None
    fechamento.save(update_fields=['status', 'fechado_por', 'fechado_em'])


def avisos(linha):
    """O que o RH deve conferir na linha antes de fechar."""
    resultado = []
    if linha.ponto_calculado.get('dias_sem_jornada'):
        resultado.append(
            f'{linha.ponto_calculado["dias_sem_jornada"]} dia(s) sem jornada cadastrada: '
            'as horas desses dias não entram no cálculo.'
        )
    if linha.ponto_calculado.get('dias_com_marcacao_impar'):
        resultado.append(f'{linha.ponto_calculado["dias_com_marcacao_impar"]} dia(s) com marcação ímpar.')
    tem_desconto_vt = linha.vt_seis_porcento or bool(linha.vt_valor)
    if nao_usa_vale_transporte(linha.funcionario) and tem_desconto_vt:
        resultado.append('Informou que não usa vale-transporte: o desconto sai ao recalcular o ponto desta pessoa.')
    elif linha.funcionario.usa_vale_transporte and not tem_desconto_vt:
        resultado.append('Usa vale-transporte, mas o desconto não foi informado (6% ou o valor creditado).')
    salario = linha.funcionario.salario
    if linha.vt_valor and salario:
        seis_porcento = (salario * SEIS_PORCENTO).quantize(Decimal('0.01'))
        if seis_porcento < linha.vt_valor:
            resultado.append(
                f'Vale-transporte: 6% do salário ({formatar_moeda(seis_porcento)}) é menor que o valor '
                'informado. O desconto deve ser 6%.'
            )
    if linha.funcionario.status != 'ativo':
        resultado.append('Funcionário não está mais ativo no cadastro.')
    return resultado
