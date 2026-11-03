from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from funcionarios.decorators import papel_required
from funcionarios.models import Usuario
from funcionarios.utils import formatar_moeda

from . import colunas, exportacao, premios, services
from .forms import RegraPremioForm, RegraProgramaForm, RegraSafraForm
from .models import (
    Fechamento, LinhaFechamento, MembroPremioPrograma, MembroPremioSafra, RegraPremio, RegraPremioPrograma,
    RegraPremioSafra, normalizar_cargo,
)


def _competencia_do_post(texto):
    """'2026-09-17' (qualquer dia do mês, vindo do calendário) ou '2026-09' -> date(2026, 9, 1); None se inválido."""
    try:
        ano, mes, *_ = map(int, (texto or '').split('-'))
        return date(ano, mes, 1)
    except ValueError:
        return None


def _celula(linha, coluna):
    celula = {
        'coluna': coluna,
        'texto': colunas.exibir_da_linha(linha, coluna),
        'marcado': bool(colunas.valor_bruto(linha, coluna)) if coluna.tipo in ('sim', 'sim_nao') else False,
        # Hora do ponto ou prêmio das regras que o RH mudou à mão (o recálculo não mexe).
        'editado': coluna.origem in ('ponto', 'regra') and linha.editado_pelo_rh(coluna.campo),
        'titulo': '',
        # Quem respondeu em Meu Perfil que não usa VT não pode ter desconto.
        'bloqueado': coluna.tipo == 'vale_transporte' and services.nao_usa_vale_transporte(linha.funcionario),
    }
    if celula['editado']:
        automatico = colunas.exibir(coluna, linha.valor_automatico(coluna.campo)) or 'em branco'
        de_onde = 'calculado pelo ponto' if coluna.origem == 'ponto' else 'pela regra de prêmios'
        celula['titulo'] = f'Ajustado à mão. Valor {de_onde}: {automatico}.'
    return celula


# O gestor opera a folha da equipe dele como o RH (lança, corrige, recalcula,
# inclui e remove), só nas linhas da equipe (`Usuario.equipe`). Abrir, fechar,
# reabrir, excluir, baixar o arquivo da contabilidade e as regras de prêmios
# valem para a empresa toda e continuam só com o DP/RH.

def _da_equipe(request, linhas):
    """As linhas que este usuário opera: todas para DP/RH, só as da equipe para o gestor."""
    usuario = request.user.usuario
    if usuario.eh_dp_ou_rh():
        return linhas
    return linhas.filter(funcionario__in=usuario.equipe())


def _linha_ou_404(request, linha_id):
    linhas = LinhaFechamento.objects.select_related('fechamento', 'funcionario')
    return get_object_or_404(_da_equipe(request, linhas), id=linha_id)


def _funcionarios_da_folha(request):
    """Quem pode ser incluído num fechamento (os ativos); para o gestor, só os da equipe."""
    funcionarios = services.funcionarios_da_folha()
    usuario = request.user.usuario
    if usuario.eh_dp_ou_rh():
        return funcionarios
    return funcionarios.filter(id__in=usuario.equipe())


def _bloqueado_se_fechado(request, fechamento):
    """Redirect de volta com aviso quando o fechamento já foi fechado; None se pode editar."""
    if fechamento.aberto:
        return None
    messages.error(request, f'O fechamento de {fechamento.competencia_extenso} já foi fechado. Reabra para alterar.')
    return redirect('folha:detalhe', fechamento_id=fechamento.id)


def _bloqueado_se_calculando(request, fechamento):
    """Não deixa fechar nem baixar com parte do ponto ainda por calcular; None se está tudo calculado."""
    if not fechamento.linhas_pendentes().exists():
        return None
    messages.error(request, 'O ponto ainda está sendo calculado. Aguarde terminar e tente de novo.')
    return redirect('folha:detalhe', fechamento_id=fechamento.id)


@login_required
@papel_required('rh', 'dp', 'gestor')
def listar_fechamentos_view(request):
    este_mes = timezone.localdate().replace(day=1)
    # Sugere o mês seguinte ao último fechamento (sem passar do mês atual); sem nenhum, o mês passado.
    ultimo = Fechamento.objects.order_by('-competencia').first()
    if ultimo is None:
        sugestao = (este_mes - timedelta(days=1)).replace(day=1)
    else:
        sugestao = min((ultimo.competencia + timedelta(days=31)).replace(day=1), este_mes)
    usuario = request.user.usuario
    if usuario.eh_dp_ou_rh():
        total_linhas = Count('linhas')
    else:
        total_linhas = Count('linhas', filter=Q(linhas__funcionario__in=usuario.equipe()))
    return render(request, 'folha/listar.html', {
        'fechamentos': Fechamento.objects.annotate(total_linhas=total_linhas),
        'sugestao': sugestao.isoformat(),
        'maximo': timezone.localdate().isoformat(),
    })


@login_required
@papel_required('rh', 'dp')
@require_POST
def abrir_fechamento_view(request):
    competencia = _competencia_do_post(request.POST.get('competencia'))
    if competencia is None:
        messages.error(request, 'Informe o mês da competência.')
        return redirect('folha:listar')
    if competencia > timezone.localdate().replace(day=1):
        messages.error(request, 'Não é possível abrir o fechamento de um mês que ainda não começou.')
        return redirect('folha:listar')

    existente = Fechamento.objects.filter(competencia=competencia).first()
    if existente is None:
        try:
            fechamento = services.abrir_fechamento(competencia, request.user)
        except IntegrityError:  # clique duplo: o outro pedido criou primeiro
            existente = Fechamento.objects.get(competencia=competencia)
        else:
            messages.success(
                request,
                f'Fechamento de {fechamento.competencia_extenso} aberto com {fechamento.linhas.count()} funcionário(s).',
            )
            return redirect('folha:detalhe', fechamento_id=fechamento.id)  # a tela calcula o ponto em seguida

    messages.info(request, f'O fechamento de {existente.competencia_extenso} já existe.')
    return redirect('folha:detalhe', fechamento_id=existente.id)


@login_required
@papel_required('rh', 'dp', 'gestor')
def detalhe_fechamento_view(request, fechamento_id):
    fechamento = get_object_or_404(Fechamento, id=fechamento_id)
    linhas = list(_da_equipe(request, fechamento.linhas.select_related('funcionario')))
    pendentes = sum(1 for linha in linhas if linha.ponto_pendente)
    # Enquanto o ponto é calculado, a tela só mostra o progresso (a grade estaria pela metade).
    grade = [] if pendentes else [
        {
            'linha': linha,
            'celulas': [_celula(linha, coluna) for coluna in colunas.COLUNAS],
            'avisos': services.avisos(linha),
        }
        for linha in linhas
    ]
    no_fechamento = {linha.funcionario_id for linha in linhas}
    _, fim = services.periodo_da_competencia(fechamento.competencia)

    return render(request, 'folha/fechamento.html', {
        'fechamento': fechamento,
        'colunas': colunas.COLUNAS,
        'grade': grade,
        'total_linhas': len(linhas),
        'pendentes': pendentes,
        'com_avisos': sum(1 for item in grade if item['avisos']),
        'fora_do_fechamento': [f for f in _funcionarios_da_folha(request) if f.id not in no_fechamento],
        'tem_regras_de_premio': (
            RegraPremioPrograma.objects.exists() or RegraPremioSafra.objects.exists() or RegraPremio.objects.exists()
        ),
        'anterior': services.fechamento_anterior(fechamento.competencia),
        'mes_em_andamento': fim >= timezone.localdate(),
    })


@login_required
@papel_required('rh', 'dp', 'gestor')
@require_POST
def salvar_celula_view(request, linha_id):
    """Grava uma célula da grade (a tela salva cada célula assim que o RH sai dela)."""
    linha = _linha_ou_404(request, linha_id)
    if not linha.fechamento.aberto:
        return JsonResponse({'erro': 'Este fechamento já foi fechado. Reabra para editar.'}, status=409)
    coluna = colunas.COLUNAS_POR_CAMPO.get(request.POST.get('campo'))
    if coluna is None or not coluna.editavel:
        return JsonResponse({'erro': 'Coluna inválida.'}, status=400)

    try:
        colunas.gravar(linha, coluna, request.POST.get('valor', ''))
    except colunas.ValorInvalido as erro:
        return JsonResponse({'erro': str(erro)}, status=400)
    if coluna.tipo == 'vale_transporte' and (linha.vt_seis_porcento or linha.vt_valor):
        if services.nao_usa_vale_transporte(linha.funcionario):
            return JsonResponse(
                {'erro': f'{linha.nome} informou que não usa vale-transporte: não pode ter desconto.'}, status=400,
            )
    # Só a(s) coluna(s) da célula: outra pessoa pode estar editando outra célula da mesma linha.
    campos = ['vt_seis_porcento', 'vt_valor'] if coluna.tipo == 'vale_transporte' else [coluna.campo]
    linha.save(update_fields=campos)

    celula = _celula(linha, coluna)
    return JsonResponse({
        'texto': celula['texto'],
        'marcado': celula['marcado'],
        'editado': celula['editado'],
        'titulo': celula['titulo'],
        'avisos': services.avisos(linha),
    })


@login_required
@papel_required('rh', 'dp', 'gestor')
@require_POST
def recalcular_fechamento_view(request, fechamento_id):
    fechamento = get_object_or_404(Fechamento, id=fechamento_id)
    bloqueio = _bloqueado_se_fechado(request, fechamento)
    if bloqueio:
        return bloqueio
    services.marcar_para_recalcular(fechamento, _da_equipe(request, fechamento.linhas.all()))  # a tela recalcula em lotes
    messages.success(
        request,
        'Recalculando o ponto. As células corrigidas à mão são mantidas; as demais são atualizadas.',
    )
    return redirect('folha:detalhe', fechamento_id=fechamento.id)


@login_required
@papel_required('rh', 'dp', 'gestor')
@require_POST
def calcular_lote_view(request, fechamento_id):
    """Um lote do cálculo do ponto; a tela chama de novo até `restantes` chegar a zero."""
    fechamento = get_object_or_404(Fechamento, id=fechamento_id)
    if not fechamento.aberto:
        return JsonResponse({'erro': 'Este fechamento já foi fechado.'}, status=409)
    linhas = _da_equipe(request, fechamento.linhas.all())
    restantes = services.calcular_pendentes(fechamento, linhas=linhas)
    return JsonResponse({'restantes': restantes, 'total': linhas.count()})


@login_required
@papel_required('rh', 'dp', 'gestor')
@require_POST
def recalcular_linha_view(request, linha_id):
    linha = _linha_ou_404(request, linha_id)
    bloqueio = _bloqueado_se_fechado(request, linha.fechamento)
    if bloqueio:
        return bloqueio
    services.recalcular_linha(linha)
    messages.success(request, f'Ponto de {linha.nome} recalculado.')
    return redirect('folha:detalhe', fechamento_id=linha.fechamento_id)


@login_required
@papel_required('rh', 'dp', 'gestor')
@require_POST
def incluir_funcionario_view(request, fechamento_id):
    fechamento = get_object_or_404(Fechamento, id=fechamento_id)
    bloqueio = _bloqueado_se_fechado(request, fechamento)
    if bloqueio:
        return bloqueio
    funcionario_id = request.POST.get('funcionario', '')
    funcionario = _funcionarios_da_folha(request).filter(id=funcionario_id).first() if funcionario_id.isdigit() else None
    if funcionario is None:
        messages.error(request, 'Selecione um funcionário ativo.')
    elif fechamento.linhas.filter(funcionario=funcionario).exists():
        messages.info(request, f'{funcionario.nome} já está neste fechamento.')
    else:
        services.nova_linha(fechamento, funcionario, services.linha_anterior(fechamento, funcionario))
        messages.success(request, f'{funcionario.nome} incluído(a) no fechamento.')
    return redirect('folha:detalhe', fechamento_id=fechamento.id)


@login_required
@papel_required('rh', 'dp', 'gestor')
@require_POST
def remover_linha_view(request, linha_id):
    linha = _linha_ou_404(request, linha_id)
    bloqueio = _bloqueado_se_fechado(request, linha.fechamento)
    if bloqueio:
        return bloqueio
    linha.delete()
    messages.success(request, f'{linha.nome} removido(a) deste fechamento.')
    return redirect('folha:detalhe', fechamento_id=linha.fechamento_id)


@login_required
@papel_required('rh', 'dp')
@require_POST
def fechar_fechamento_view(request, fechamento_id):
    fechamento = get_object_or_404(Fechamento, id=fechamento_id)
    bloqueio = _bloqueado_se_calculando(request, fechamento)
    if bloqueio:
        return bloqueio
    if fechamento.aberto:
        services.fechar(fechamento, request.user)
        messages.success(request, f'Fechamento de {fechamento.competencia_extenso} fechado. Os valores estão travados.')
    return redirect('folha:detalhe', fechamento_id=fechamento.id)


@login_required
@papel_required('rh', 'dp')
@require_POST
def reabrir_fechamento_view(request, fechamento_id):
    fechamento = get_object_or_404(Fechamento, id=fechamento_id)
    if not fechamento.aberto:
        services.reabrir(fechamento)
        messages.success(request, f'Fechamento de {fechamento.competencia_extenso} reaberto para edição.')
    return redirect('folha:detalhe', fechamento_id=fechamento.id)


@login_required
@papel_required('rh', 'dp')
@require_POST
def excluir_fechamento_view(request, fechamento_id):
    fechamento = get_object_or_404(Fechamento, id=fechamento_id)
    bloqueio = _bloqueado_se_fechado(request, fechamento)
    if bloqueio:
        return bloqueio
    competencia = fechamento.competencia_extenso
    fechamento.delete()
    messages.success(request, f'Fechamento de {competencia} excluído.')
    return redirect('folha:listar')


def _baixar(conteudo, nome, tipo):
    resposta = HttpResponse(conteudo, content_type=tipo)
    resposta['Content-Disposition'] = f'attachment; filename="{nome}"'
    return resposta


@login_required
@papel_required('rh', 'dp')
def exportar_xlsx_view(request, fechamento_id):
    fechamento = get_object_or_404(Fechamento, id=fechamento_id)
    bloqueio = _bloqueado_se_calculando(request, fechamento)
    if bloqueio:
        return bloqueio
    return _baixar(
        exportacao.gerar_xlsx(fechamento),
        exportacao.nome_arquivo(fechamento, 'xlsx'),
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


@login_required
@papel_required('rh', 'dp')
def exportar_pdf_view(request, fechamento_id):
    fechamento = get_object_or_404(Fechamento, id=fechamento_id)
    bloqueio = _bloqueado_se_calculando(request, fechamento)
    if bloqueio:
        return bloqueio
    try:
        gerado_por = request.user.usuario.funcionario.nome
    except Usuario.DoesNotExist:
        gerado_por = request.user.get_username()
    pdf = exportacao.gerar_pdf(fechamento, gerado_por)
    if pdf is None:
        messages.error(request, 'Não foi possível gerar o PDF. Tente novamente.')
        return redirect('folha:detalhe', fechamento_id=fechamento.id)
    return _baixar(pdf, exportacao.nome_arquivo(fechamento, 'pdf'), 'application/pdf')


# ---------- Regras de prêmios ----------

# Os prêmios das `RegraPremio` (setor, cargo ou pessoa). Programa e safra têm
# telas próprias: regras com a lista de quem recebe.
DESCRICAO_PREMIO = {
    'premio_incentivo': 'Valor fixo por cargo e setor, ou para pessoas específicas.',
}


def _valor_texto(tipo, valor):
    if not valor:
        return 'não recebe'
    return colunas.exibir(colunas.COLUNAS_POR_CAMPO[tipo], valor)


def _fechamento_base():
    """O último fechamento fechado: é com ele que a tela confere as regras."""
    return Fechamento.objects.filter(status='fechado').order_by('-competencia').first()


def _cargos_existentes(funcionarios):
    return sorted({' '.join(f.cargo.split()) for f in funcionarios if f.cargo})


def _secoes_de_premios():
    """Uma seção por prêmio: as regras, quem recebe neste mês e a conferência com o último fechamento."""
    competencia = timezone.localdate().replace(day=1)
    regras_do_mes = premios.RegrasDoMes(competencia)
    ativos = list(services.funcionarios_da_folha().select_related('setor'))
    base = _fechamento_base()

    secoes = []
    for tipo, rotulo in RegraPremio.TIPO_CHOICES:
        quem_recebe, vencedoras = [], Counter()
        for funcionario in ativos:
            regra = regras_do_mes.regra_para(funcionario, tipo)
            if regra is None:
                continue
            vencedoras[regra.id] += 1
            if regra.valor:
                quem_recebe.append({'funcionario': funcionario, 'regra': regra, 'valor': _valor_texto(tipo, regra.valor)})

        regras = list(RegraPremio.objects.filter(tipo=tipo).select_related('setor', 'funcionario'))
        for regra in regras:
            regra.valor_texto = _valor_texto(tipo, regra.valor)
            regra.pessoas_no_mes = vencedoras[regra.id]
            regra.vale_neste_mes = regra.vale_em(competencia)

        diferencas = premios.diferencas(base, tipo) if base else []
        for item in diferencas:
            item['no_fechamento_texto'] = _valor_texto(tipo, item['no_fechamento'])
            item['pela_regra_texto'] = _valor_texto(tipo, item['pela_regra'])

        secoes.append({
            'tipo': tipo,
            'rotulo': rotulo,
            'rubrica': colunas.COLUNAS_POR_CAMPO[tipo].rubrica,
            'descricao': DESCRICAO_PREMIO[tipo],
            'regras': regras,
            'quem_recebe': quem_recebe,
            'diferencas': diferencas,
            'sugestoes': premios.sugestoes_por_setor_e_cargo(base, tipo) if base else [],
        })
    return competencia, base, secoes, ativos


def _mensagem_regra(request, regra, acao):
    pessoas = sum(1 for f in services.funcionarios_da_folha() if regra.atinge(f))
    messages.success(
        request,
        f'Regra {acao}: {regra.get_tipo_display().lower()} de {regra.descricao_alvo} '
        f'({_valor_texto(regra.tipo, regra.valor)}, {regra.descricao_vigencia}). Atinge {pessoas} pessoa(s) ativa(s). '
        'Fechamentos abertos pegam a mudança ao recalcular.',
    )


def _voltar_para_premios(tipo):
    return redirect(reverse('folha:premios') + f'#{tipo}')


# ---------- Prêmios com a lista de quem recebe (programa e safra) ----------
# `relacionado` é o nome do vínculo no Funcionario: 'membro_premio_programa' ou 'membro_premio_safra'.

AVISO_RECALCULAR = 'Fechamentos abertos pegam a mudança ao recalcular; os já fechados não mudam.'


def _regra_de(funcionario, relacionado):
    """A regra do prêmio em que a pessoa está, ou None."""
    membro = getattr(funcionario, relacionado, None)  # sem vínculo, o Django levanta um AttributeError
    return membro.regra if membro else None


def _resumir(regra):
    """Quantos ativos a regra tem e de quais setores (as listas guardam também quem saiu da empresa)."""
    ativos = [membro.funcionario for membro in regra.membros.all() if membro.funcionario.status == 'ativo']
    regra.total_ativos = len(ativos)
    regra.setores = sorted({funcionario.setor.nome for funcionario in ativos if funcionario.setor})


def _candidatos(regra, relacionado, descrever, sugerido):
    """
    Quem pode entrar na regra, na ordem em que costuma ser escolhido: os
    `sugerido` sem regra, os outros sem regra e, por último, quem já está em
    outra regra do mesmo prêmio (e sai de lá se for colocado nesta).
    """
    candidatos = []
    for funcionario in services.funcionarios_da_folha().select_related('setor', f'{relacionado}__regra'):
        atual = _regra_de(funcionario, relacionado)
        if atual == regra:
            continue
        candidatos.append({
            'funcionario': funcionario,
            'regra_atual': descrever(atual) if atual else '',
            'ordem': 2 if atual else (0 if sugerido(funcionario) else 1),
        })
    candidatos.sort(key=lambda item: item['ordem'])
    return candidatos


def _colocar_na_regra(request, regra, relacionado, descrever):
    """Coloca os marcados na regra; quem estava em outra regra do mesmo prêmio sai de lá."""
    ids = [valor for valor in request.POST.getlist('funcionario') if valor.isdigit()]
    funcionarios = list(services.funcionarios_da_folha().filter(id__in=ids).select_related(f'{relacionado}__regra'))
    if not funcionarios:
        messages.error(request, 'Selecione ao menos um colaborador ativo.')
        return
    incluidos, movidos = 0, []
    with transaction.atomic():
        for funcionario in funcionarios:
            anterior = _regra_de(funcionario, relacionado)
            if anterior == regra:
                continue
            if anterior:
                getattr(funcionario, relacionado).delete()
                movidos.append(f'{funcionario.nome} (saiu de {descrever(anterior)})')
            regra.membros.create(funcionario=funcionario, incluido_por=request.user)
            incluidos += 1
    texto = f'{incluidos} pessoa(s) colocada(s) em {descrever(regra)}.'
    if movidos:
        texto += ' Mudaram de regra: ' + '; '.join(movidos) + '.'
    messages.success(request, f'{texto} {AVISO_RECALCULAR}')


def _secao_programa(base, ativos):
    """As regras de programa (cargo = valor, com quem recebe), quem está sem regra e a conferência com `base`."""
    regras = list(RegraPremioPrograma.objects.prefetch_related('membros__funcionario__setor'))
    regras_do_cargo = defaultdict(list)
    na_lista = set()
    for regra in regras:
        _resumir(regra)
        regra.valor_texto = formatar_moeda(regra.valor)
        regras_do_cargo[normalizar_cargo(regra.cargo)].append(regra)
        na_lista.update(membro.funcionario_id for membro in regra.membros.all())

    # Primeiro quem tem cargo que já tem regra: é o caso do contratado novo, que só falta colocar.
    sem_regra = sorted(
        (
            {'funcionario': funcionario, 'regras_do_cargo': regras_do_cargo.get(normalizar_cargo(funcionario.cargo), [])}
            for funcionario in ativos if funcionario.id not in na_lista
        ),
        key=lambda item: not item['regras_do_cargo'],
    )

    # Todo mês começa com o valor cheio da regra e a redução (atestado, faltas) é lançada à mão na grade:
    # receber menos que a regra é o normal. Só interessa quem recebeu sem estar numa regra ou acima dela.
    diferencas = [
        item for item in (premios.diferencas(base, 'premio_programa') if base else [])
        if item['regra'] is None or item['no_fechamento'] > item['pela_regra']
    ]
    for item in diferencas:
        item['no_fechamento_texto'] = _valor_texto('premio_programa', item['no_fechamento'])
        item['pela_regra_texto'] = _valor_texto('premio_programa', item['pela_regra'])
    return {
        'regras': regras,
        'recebem': sum(regra.total_ativos for regra in regras),
        'sem_regra': sem_regra,
        'sem_regra_com_cargo': sum(1 for item in sem_regra if item['regras_do_cargo']),
        'diferencas': diferencas,
        'rubrica': colunas.COLUNAS_POR_CAMPO['premio_programa'].rubrica,
    }


def _secao_safra(base, competencia):
    """As regras de safra (meses e % do salário, com quem recebe) e quem recebeu em `base` sem estar em nenhuma."""
    regras = list(RegraPremioSafra.objects.prefetch_related('membros__funcionario__setor'))
    for regra in regras:
        _resumir(regra)
        regra.percentual_texto = _valor_texto('premio_safra', regra.percentual)
        regra.vale_neste_mes = regra.vale_em(competencia)
    # Quem só falta colocar numa regra: em set/2026, os 5 que receberam 10% pela planilha.
    na_lista = set(MembroPremioSafra.objects.values_list('funcionario_id', flat=True))
    linhas = base.linhas.select_related('funcionario__setor') if base else []
    receberam_sem_regra = [
        {'linha': linha, 'texto': _valor_texto('premio_safra', linha.premio_safra)}
        for linha in linhas
        if linha.premio_safra and linha.funcionario.status == 'ativo' and linha.funcionario_id not in na_lista
    ]
    return {
        'regras': regras,
        'recebem': sum(regra.total_ativos for regra in regras if regra.vale_neste_mes),
        'receberam_sem_regra': receberam_sem_regra,
    }


def _pagina_de_premios(request, form=None, form_programa=None, form_safra=None):
    competencia, base, secoes, ativos = _secoes_de_premios()
    return render(request, 'folha/premios.html', {
        'form': form or RegraPremioForm(),
        'form_programa': form_programa or RegraProgramaForm(),
        'form_safra': form_safra or RegraSafraForm(),
        'programa': _secao_programa(base, ativos),
        'safra': _secao_safra(base, competencia),
        'secoes': secoes,
        'competencia': competencia,
        'base': base,
        'cargos_existentes': _cargos_existentes(ativos),
        'fechamento_aberto': Fechamento.objects.filter(status='aberto').order_by('-competencia').first(),
    })


@login_required
@papel_required('rh', 'dp')
def premios_view(request):
    form = None
    if request.method == 'POST':
        form = RegraPremioForm(request.POST)
        if form.is_valid():
            regra = form.save(commit=False)
            regra.atualizado_por = request.user
            regra.save()
            _mensagem_regra(request, regra, 'criada')
            return _voltar_para_premios(regra.tipo)
    return _pagina_de_premios(request, form=form)


# ---------- Prêmio programa: regra por cargo, com a lista de quem recebe ----------

def _descricao_regra_programa(regra):
    return f'{regra.cargo} · {formatar_moeda(regra.valor)}'


@login_required
@papel_required('rh', 'dp')
@require_POST
def criar_regra_programa_view(request):
    form = RegraProgramaForm(request.POST)
    if not form.is_valid():
        return _pagina_de_premios(request, form_programa=form)
    regra = form.save(commit=False)
    regra.atualizado_por = request.user
    regra.save()
    messages.success(request, f'Regra criada: {_descricao_regra_programa(regra)}. Agora coloque nela quem recebe.')
    return redirect('folha:regra_programa', regra_id=regra.id)


@login_required
@papel_required('rh', 'dp')
def regra_programa_view(request, regra_id):
    """A regra (cargo e valor, editáveis), quem está nela e a escolha de quem colocar."""
    regra = get_object_or_404(RegraPremioPrograma, id=regra_id)
    if request.method == 'POST':
        form = RegraProgramaForm(request.POST, instance=regra)
        if form.is_valid():
            regra = form.save(commit=False)
            regra.atualizado_por = request.user
            regra.save()
            messages.success(request, f'Regra atualizada: {_descricao_regra_programa(regra)}. {AVISO_RECALCULAR}')
            return redirect('folha:regra_programa', regra_id=regra.id)
        regra.refresh_from_db()  # o título da página fica com o que está gravado
    else:
        form = RegraProgramaForm(instance=regra)

    cargo = normalizar_cargo(regra.cargo)
    membros = list(regra.membros.select_related('funcionario__setor'))
    for membro in membros:
        if normalizar_cargo(membro.funcionario.cargo) != cargo:
            membro.aviso, membro.aviso_titulo = 'outro cargo', 'O cargo no cadastro não é o desta regra'

    candidatos = _candidatos(
        regra, 'membro_premio_programa', _descricao_regra_programa,
        sugerido=lambda funcionario: normalizar_cargo(funcionario.cargo) == cargo,
    )
    sugeridos = sum(1 for item in candidatos if item['ordem'] == 0)
    return render(request, 'folha/regra_programa.html', {
        'regra': regra,
        'valor_texto': formatar_moeda(regra.valor),
        'form': form,
        'membros': membros,
        'candidatos': candidatos,
        'ajuda_escolha': f'No topo, {sugeridos} pessoa(s) com o cargo desta regra e sem regra nenhuma.' if sugeridos else '',
        'cargos_existentes': _cargos_existentes(services.funcionarios_da_folha()),
    })


@login_required
@papel_required('rh', 'dp')
@require_POST
def incluir_no_programa_view(request, regra_id):
    """Coloca uma ou mais pessoas na regra; quem estava em outra regra de programa sai de lá."""
    regra = get_object_or_404(RegraPremioPrograma, id=regra_id)
    _colocar_na_regra(request, regra, 'membro_premio_programa', _descricao_regra_programa)
    if request.POST.get('voltar') == 'premios':
        return redirect(reverse('folha:premios') + '#premio_programa')
    return redirect('folha:regra_programa', regra_id=regra.id)


@login_required
@papel_required('rh', 'dp')
@require_POST
def remover_do_programa_view(request, regra_id, funcionario_id):
    membro = get_object_or_404(
        MembroPremioPrograma.objects.select_related('funcionario', 'regra'), regra_id=regra_id, funcionario_id=funcionario_id,
    )
    membro.delete()
    messages.success(
        request,
        f'{membro.funcionario.nome} saiu de {_descricao_regra_programa(membro.regra)} e fica sem prêmio programa. {AVISO_RECALCULAR}',
    )
    return redirect('folha:regra_programa', regra_id=regra_id)


@login_required
@papel_required('rh', 'dp')
@require_POST
def excluir_regra_programa_view(request, regra_id):
    regra = get_object_or_404(RegraPremioPrograma, id=regra_id)
    descricao, pessoas = _descricao_regra_programa(regra), regra.membros.count()
    regra.delete()  # as pessoas da lista saem junto
    messages.success(
        request, f'Regra excluída: {descricao}. {pessoas} pessoa(s) ficaram sem prêmio programa. {AVISO_RECALCULAR}',
    )
    return _voltar_para_premios('premio_programa')


# ---------- Bônus safra: % do salário nos meses da safra, com a lista de quem recebe ----------

def _descricao_regra_safra(regra):
    return f'{regra.nome} · {_valor_texto("premio_safra", regra.percentual)}'


@login_required
@papel_required('rh', 'dp')
@require_POST
def criar_regra_safra_view(request):
    form = RegraSafraForm(request.POST)
    if not form.is_valid():
        return _pagina_de_premios(request, form_safra=form)
    regra = form.save(commit=False)
    regra.atualizado_por = request.user
    regra.save()
    messages.success(request, f'Regra criada: {_descricao_regra_safra(regra)}. Agora coloque nela quem recebe.')
    return redirect('folha:regra_safra', regra_id=regra.id)


@login_required
@papel_required('rh', 'dp')
def regra_safra_view(request, regra_id):
    """A regra (nome, percentual e meses, editáveis), quem está nela e a escolha de quem colocar."""
    regra = get_object_or_404(RegraPremioSafra, id=regra_id)
    if request.method == 'POST':
        form = RegraSafraForm(request.POST, instance=regra)
        if form.is_valid():
            regra = form.save(commit=False)
            regra.atualizado_por = request.user
            regra.save()
            messages.success(request, f'Regra atualizada: {_descricao_regra_safra(regra)}. {AVISO_RECALCULAR}')
            return redirect('folha:regra_safra', regra_id=regra.id)
        regra.refresh_from_db()  # o título da página fica com o que está gravado
    else:
        form = RegraSafraForm(instance=regra)

    membros = list(regra.membros.select_related('funcionario__setor'))
    # O safra é de setores específicos: no topo, quem é dos setores de quem já está na regra.
    setores = {membro.funcionario.setor_id for membro in membros if membro.funcionario.setor_id}
    candidatos = _candidatos(
        regra, 'membro_premio_safra', _descricao_regra_safra,
        sugerido=lambda funcionario: funcionario.setor_id in setores,
    )
    sugeridos = sum(1 for item in candidatos if item['ordem'] == 0)
    return render(request, 'folha/regra_safra.html', {
        'regra': regra,
        'percentual_texto': _valor_texto('premio_safra', regra.percentual),
        'vale_neste_mes': regra.vale_em(timezone.localdate().replace(day=1)),
        'form': form,
        'membros': membros,
        'candidatos': candidatos,
        'ajuda_escolha': f'No topo, {sugeridos} pessoa(s) dos setores desta regra e sem regra nenhuma.' if sugeridos else '',
    })


@login_required
@papel_required('rh', 'dp')
@require_POST
def incluir_na_safra_view(request, regra_id):
    """Coloca uma ou mais pessoas na regra; quem estava em outra regra de safra sai de lá."""
    regra = get_object_or_404(RegraPremioSafra, id=regra_id)
    _colocar_na_regra(request, regra, 'membro_premio_safra', _descricao_regra_safra)
    if request.POST.get('voltar') == 'premios':
        return redirect(reverse('folha:premios') + '#premio_safra')
    return redirect('folha:regra_safra', regra_id=regra.id)


@login_required
@papel_required('rh', 'dp')
@require_POST
def remover_da_safra_view(request, regra_id, funcionario_id):
    membro = get_object_or_404(
        MembroPremioSafra.objects.select_related('funcionario', 'regra'), regra_id=regra_id, funcionario_id=funcionario_id,
    )
    membro.delete()
    messages.success(
        request,
        f'{membro.funcionario.nome} saiu de {_descricao_regra_safra(membro.regra)} e fica sem bônus safra. {AVISO_RECALCULAR}',
    )
    return redirect('folha:regra_safra', regra_id=regra_id)


@login_required
@papel_required('rh', 'dp')
@require_POST
def excluir_regra_safra_view(request, regra_id):
    regra = get_object_or_404(RegraPremioSafra, id=regra_id)
    descricao, pessoas = _descricao_regra_safra(regra), regra.membros.count()
    regra.delete()  # as pessoas da lista saem junto
    messages.success(
        request, f'Regra excluída: {descricao}. {pessoas} pessoa(s) ficaram sem bônus safra. {AVISO_RECALCULAR}',
    )
    return _voltar_para_premios('premio_safra')


@login_required
@papel_required('rh', 'dp')
def editar_regra_view(request, regra_id):
    regra = get_object_or_404(RegraPremio, id=regra_id)
    if request.method == 'POST':
        form = RegraPremioForm(request.POST, instance=regra)
        if form.is_valid():
            regra = form.save(commit=False)
            regra.atualizado_por = request.user
            regra.save()
            _mensagem_regra(request, regra, 'atualizada')
            return _voltar_para_premios(regra.tipo)
    else:
        form = RegraPremioForm(instance=regra)
    return render(request, 'folha/regra_form.html', {
        'form': form,
        'regra': regra,
        'cargos_existentes': _cargos_existentes(services.funcionarios_da_folha()),
    })


@login_required
@papel_required('rh', 'dp')
@require_POST
def excluir_regra_view(request, regra_id):
    regra = get_object_or_404(RegraPremio, id=regra_id)
    tipo, descricao = regra.tipo, f'{regra.get_tipo_display().lower()} de {regra.descricao_alvo}'
    regra.delete()
    messages.success(request, f'Regra excluída: {descricao}. Fechamentos abertos pegam a mudança ao recalcular.')
    return _voltar_para_premios(tipo)


@login_required
@papel_required('rh', 'dp')
@require_POST
def sugerir_regras_view(request):
    """Cria as regras de setor e cargo sugeridas a partir do último fechamento fechado."""
    tipo = request.POST.get('tipo')
    base = _fechamento_base()
    if tipo not in DESCRICAO_PREMIO or base is None:
        messages.error(request, 'Não há sugestões para criar.')
        return redirect('folha:premios')
    criadas = 0
    for sugestao in premios.sugestoes_por_setor_e_cargo(base, tipo):
        regra = RegraPremio(
            tipo=tipo, setor=sugestao['setor'], cargo=sugestao['cargo'], valor=sugestao['valor'],
            atualizado_por=request.user,
        )
        try:
            regra.full_clean()
        except ValidationError:
            continue  # já existe regra para esse alvo: a que o RH cadastrou prevalece
        regra.save()
        criadas += 1
    messages.success(
        request,
        f'{criadas} regra(s) de setor e cargo criada(s) a partir de {base.competencia_extenso}. '
        'Confira as diferenças: quem recebeu outro valor não virou regra.',
    )
    return _voltar_para_premios(tipo)


@login_required
@papel_required('rh', 'dp')
@require_POST
def fixar_regra_view(request):
    """Regra de pessoa com o valor que ela recebeu no último fechamento (zero = "não recebe")."""
    tipo = request.POST.get('tipo')
    funcionario_id = request.POST.get('funcionario', '')
    funcionario = services.funcionarios_da_folha().filter(id=funcionario_id).first() if funcionario_id.isdigit() else None
    try:
        valor = Decimal(request.POST.get('valor', ''))
    except InvalidOperation:
        valor = None
    if tipo not in DESCRICAO_PREMIO or funcionario is None or valor is None:
        messages.error(request, 'Não foi possível criar a regra.')
        return redirect('folha:premios')
    regra = RegraPremio(tipo=tipo, funcionario=funcionario, valor=valor, atualizado_por=request.user)
    try:
        regra.full_clean()
    except ValidationError as erro:
        messages.error(request, ' '.join(erro.messages))
        return _voltar_para_premios(tipo)
    regra.save()
    _mensagem_regra(request, regra, 'criada')
    return _voltar_para_premios(tipo)
