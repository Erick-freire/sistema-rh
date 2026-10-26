from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import ProtectedError, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from funcionarios import notificacoes
from funcionarios.decorators import papel_required
from funcionarios.models import Funcionario, Usuario

from . import services
from .calculos import calcular_periodo, situacao_do_dia, valores_do_periodo
from .forms import (
    AjustarMarcacaoForm, DIAS_SEMANA_INICIAIS, EquipamentoForm, FeriadoForm, FuncionarioJornadaForm,
    HorarioDiaFormSet, HorarioForm, RejeitarAjusteForm, SolicitarAjusteForm,
)
from .models import AjustePonto, Equipamento, Feriado, FuncionarioJornada, Horario, HorarioDia, MarcacaoRelogio
from .utils import intervalo_do_dia

JANELA_MEU_PONTO_DIAS = 30
JANELA_MEU_PONTO_MAXIMA_DIAS = 90


def _data_do_get(request, campo, default):
    """GET de data com fallback silencioso pro default se vier vazio/malformado -- é conteúdo digitável na URL por qualquer funcionário, não só RH."""
    valor = request.GET.get(campo)
    if not valor:
        return default
    try:
        return date.fromisoformat(valor)
    except ValueError:
        return default


def _dias_com_forms(formset):
    """
    Zip nomes de dia da semana com os forms do formset -- mais simples do que
    tentar ler `form.instance.dia_semana` no template (não dá pra confiar
    nisso para os forms "extra" de um horário novo, que ainda não têm
    instância salva).
    """
    nomes = [nome for _, nome in HorarioDia.DIA_SEMANA_CHOICES]
    return list(zip(nomes, formset.forms))


@login_required
@papel_required('rh', 'dp')
def listar_equipamentos_view(request):
    equipamentos = Equipamento.objects.all()
    return render(request, 'ponto/listar_equipamentos.html', {
        'equipamentos': equipamentos,
    })


@login_required
@papel_required('rh', 'dp')
def cadastrar_equipamento_view(request):
    if request.method == 'POST':
        form = EquipamentoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Equipamento cadastrado com sucesso.')
            return redirect('ponto:listar_equipamentos')
    else:
        form = EquipamentoForm()

    return render(request, 'ponto/form_equipamento.html', {'form': form, 'equipamento': None})


@login_required
@papel_required('rh', 'dp')
def editar_equipamento_view(request, equipamento_id):
    equipamento = get_object_or_404(Equipamento, id=equipamento_id)

    if request.method == 'POST':
        form = EquipamentoForm(request.POST, instance=equipamento)
        if form.is_valid():
            form.save()
            messages.success(request, 'Equipamento atualizado com sucesso.')
            return redirect('ponto:listar_equipamentos')
    else:
        form = EquipamentoForm(instance=equipamento)

    return render(request, 'ponto/form_equipamento.html', {'form': form, 'equipamento': equipamento})


@login_required
@papel_required('rh', 'dp')
def testar_conexao_equipamento_view(request, equipamento_id):
    equipamento = get_object_or_404(Equipamento, id=equipamento_id)

    ok, erro = services.testar_conexao(equipamento)
    if ok:
        messages.success(request, f'Conexão com "{equipamento.nome}" funcionando normalmente.')
    else:
        messages.error(request, f'Não foi possível conectar a "{equipamento.nome}": {erro}')

    return redirect('ponto:listar_equipamentos')


@login_required
@papel_required('rh', 'dp')
def alternar_status_equipamento_view(request, equipamento_id):
    equipamento = get_object_or_404(Equipamento, id=equipamento_id)
    equipamento.ativo = not equipamento.ativo
    equipamento.save(update_fields=['ativo', 'atualizado_em'])

    if equipamento.ativo:
        messages.success(request, f'"{equipamento.nome}" reativado. Volta a receber sincronização automática.')
    else:
        messages.success(request, f'"{equipamento.nome}" desativado. Não recebe mais sincronizações novas.')

    return redirect('ponto:listar_equipamentos')


@login_required
@papel_required('rh', 'dp')
def excluir_equipamento_view(request, equipamento_id):
    equipamento = get_object_or_404(Equipamento, id=equipamento_id)
    nome = equipamento.nome

    try:
        equipamento.delete()
        messages.success(request, f'"{nome}" excluído.')
    except ProtectedError:
        messages.error(
            request,
            f'Não é possível excluir "{nome}": já existem marcações coletadas dele. '
            'Desative o equipamento em vez de excluir, para preservar o histórico de ponto.',
        )

    return redirect('ponto:listar_equipamentos')


@login_required
@papel_required('rh', 'dp')
def enviar_equipamento_view(request, equipamento_id):
    """
    "Enviar": só processa os cadastros pendentes desse equipamento (igual ao
    Enviar do Secullum) -- separado de "Receber" pra quem só quer ver as
    marcações novas não precisar esperar o envio de cadastro, que pode
    demorar bem mais quando tem muita pendência acumulada.
    """
    equipamento = get_object_or_404(Equipamento, id=equipamento_id)
    processadas, sucesso = services.processar_pendencias(equipamento=equipamento)

    if not processadas:
        messages.success(request, f'"{equipamento.nome}": nenhum cadastro pendente.')
    elif sucesso < processadas:
        messages.error(request, f'"{equipamento.nome}": {sucesso}/{processadas} cadastro(s) enviado(s). O restante será tentado de novo automaticamente.')
    else:
        messages.success(request, f'"{equipamento.nome}": {sucesso}/{processadas} cadastro(s) enviado(s).')

    return redirect('ponto:listar_equipamentos')


@login_required
@papel_required('rh', 'dp')
def receber_equipamento_view(request, equipamento_id):
    """"Receber": só puxa as marcações novas desse equipamento (igual ao Receber do Secullum)."""
    equipamento = get_object_or_404(Equipamento, id=equipamento_id)
    marcacoes_novas = services.coletar_marcacoes(equipamento)
    messages.success(request, f'"{equipamento.nome}": {marcacoes_novas} marcação(ões) nova(s).')
    return redirect('ponto:listar_equipamentos')


@login_required
@papel_required('rh', 'dp')
def enviar_todos_view(request):
    processadas, sucesso = services.processar_pendencias()

    if not processadas:
        messages.success(request, 'Nenhum cadastro pendente.')
    elif sucesso < processadas:
        messages.error(request, f'{sucesso}/{processadas} cadastro(s) enviado(s). O restante será tentado de novo automaticamente.')
    else:
        messages.success(request, f'{sucesso}/{processadas} cadastro(s) enviado(s).')

    return redirect('ponto:listar_equipamentos')


@login_required
@papel_required('rh', 'dp')
def receber_todos_view(request):
    total_marcacoes = sum(services.coletar_marcacoes(e) for e in Equipamento.objects.filter(ativo=True))
    messages.success(request, f'{total_marcacoes} marcação(ões) nova(s).')
    return redirect('ponto:listar_equipamentos')


@login_required
@papel_required('rh', 'dp')
def listar_marcacoes_view(request):
    funcionario_id = request.GET.get('funcionario')
    data = request.GET.get('data')

    marcacoes = MarcacaoRelogio.objects.select_related('funcionario', 'equipamento')
    if funcionario_id:
        marcacoes = marcacoes.filter(funcionario_id=funcionario_id)
    if data:
        inicio, fim = intervalo_do_dia(date.fromisoformat(data))
        marcacoes = marcacoes.filter(momento__gte=inicio, momento__lt=fim)

    return render(request, 'ponto/listar_marcacoes.html', {
        'marcacoes': marcacoes[:500],
        'funcionario_id': funcionario_id or '',
        'data': data or '',
    })


@login_required
@papel_required('rh', 'dp')
def listar_horarios_view(request):
    horarios = Horario.objects.prefetch_related('dias')
    feriados = Feriado.objects.all()
    return render(request, 'ponto/listar_horarios.html', {'horarios': horarios, 'feriados': feriados})


@login_required
@papel_required('rh', 'dp')
def cadastrar_horario_view(request):
    if request.method == 'POST':
        form = HorarioForm(request.POST)
        formset = HorarioDiaFormSet(request.POST, instance=form.instance)
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                horario = form.save()
                formset.instance = horario
                formset.save()
            messages.success(request, f'Horário {horario.numero} cadastrado com sucesso.')
            return redirect('ponto:listar_horarios')
    else:
        form = HorarioForm()
        formset = HorarioDiaFormSet(initial=DIAS_SEMANA_INICIAIS)

    return render(request, 'ponto/form_horario.html', {
        'form': form, 'formset': formset, 'horario': None, 'dias_com_forms': _dias_com_forms(formset),
    })


@login_required
@papel_required('rh', 'dp')
def editar_horario_view(request, horario_id):
    horario = get_object_or_404(Horario, id=horario_id)

    if request.method == 'POST':
        form = HorarioForm(request.POST, instance=horario)
        formset = HorarioDiaFormSet(request.POST, instance=horario)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, f'Horário {horario.numero} atualizado com sucesso.')
            return redirect('ponto:listar_horarios')
    else:
        form = HorarioForm(instance=horario)
        initial = DIAS_SEMANA_INICIAIS if not horario.dias.exists() else None
        formset = HorarioDiaFormSet(instance=horario, initial=initial)

    # O horário não guarda histórico: o espelho de qualquer dia é calculado com
    # os horários atuais. Quem usa ou já usou este horário tem os dias passados
    # recalculados se ele for alterado -- a tela avisa antes de salvar.
    pessoas_vinculadas = FuncionarioJornada.objects.filter(horario=horario).values('funcionario').distinct().count()

    return render(request, 'ponto/form_horario.html', {
        'form': form, 'formset': formset, 'horario': horario, 'dias_com_forms': _dias_com_forms(formset),
        'pessoas_vinculadas': pessoas_vinculadas,
    })


@login_required
@papel_required('rh', 'dp')
def excluir_horario_view(request, horario_id):
    horario = get_object_or_404(Horario, id=horario_id)
    numero = horario.numero
    horario.delete()
    messages.success(request, f'Horário {numero} excluído.')
    return redirect('ponto:listar_horarios')


@login_required
@papel_required('rh', 'dp')
def ajustar_marcacao_view(request, funcionario_id, data):
    """
    Lançamento manual de uma marcação (batida esquecida ou correção de uma
    batida errada), a partir de um clique num Entrada/Saída do espelho de
    Cálculo. Nunca mexe na MarcacaoRelogio original -- só cria um AjustePonto
    que o motor de cálculo passa a considerar no lugar dela (ou a mais, se
    for uma batida que faltou por completo).

    A `data` da URL é a linha do espelho (dia em que o turno começou) e só
    serve de sugestão: o ajuste guarda a data e a hora REAIS da batida, que o
    RH confirma ou troca no formulário.
    """
    funcionario = get_object_or_404(Funcionario, id=funcionario_id)
    data_convertida = date.fromisoformat(data)

    marcacao_original = None
    marcacao_original_id = request.GET.get('marcacao_original') or request.POST.get('marcacao_original')
    if marcacao_original_id:
        marcacao_original = get_object_or_404(MarcacaoRelogio, id=marcacao_original_id, funcionario=funcionario)

    voltar = request.GET.get('voltar') or request.POST.get('voltar')
    url_voltar = voltar if voltar and url_has_allowed_host_and_scheme(
        voltar, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ) else f"{reverse('ponto:listar_calculo')}?funcionario={funcionario.id}"

    if request.method == 'POST':
        form = AjustarMarcacaoForm(request.POST)
        if form.is_valid():
            ajuste = form.save(commit=False)
            ajuste.funcionario = funcionario
            ajuste.marcacao_original = marcacao_original

            ja_existe_aprovado = AjustePonto.objects.filter(
                funcionario=funcionario, data=ajuste.data, tipo_evento=ajuste.tipo_evento, status='aprovado',
            ).exists()
            if ja_existe_aprovado:
                messages.error(
                    request,
                    f'Já existe um ajuste aprovado pra {funcionario.nome} em {ajuste.data.strftime("%d/%m/%Y")} '
                    f'({ajuste.get_tipo_evento_display()}). Corrija ou remova o ajuste existente antes de lançar outro.',
                )
                return redirect(url_voltar)

            # Lançado direto pelo RH: já sai aprovado, sem passar por fila de
            # solicitação -- é o mesmo fluxo de sempre, só ganhou os campos novos.
            ajuste.usuario = request.user
            ajuste.status = 'aprovado'
            ajuste.solicitado_por = request.user
            ajuste.analisado_por = request.user
            ajuste.analisado_em = timezone.now()
            ajuste.save()
            # Diz exatamente o que foi gravado, pro RH conferir a data na hora.
            messages.success(
                request,
                f'Ajuste de ponto de {funcionario.nome} registrado: {ajuste.get_tipo_evento_display()} às '
                f'{ajuste.horario_ajustado.strftime("%H:%M")} de {ajuste.data.strftime("%d/%m/%Y")}.',
            )
            return redirect(url_voltar)
    else:
        inicial = {'tipo_evento': request.GET.get('tipo') or 'entrada', 'data': data_convertida}
        if marcacao_original:
            momento_original = timezone.localtime(marcacao_original.momento)
            inicial['data'] = momento_original.date()
            inicial['horario_ajustado'] = momento_original.time()
        form = AjustarMarcacaoForm(initial=inicial)

    return render(request, 'ponto/form_ajuste.html', {
        'form': form,
        'funcionario': funcionario,
        'data': data_convertida,
        'marcacao_original': marcacao_original,
        'voltar': url_voltar,
    })


@login_required
@papel_required('rh', 'dp')
def listar_calculo_view(request):
    hoje = timezone.now().date()
    data_inicio = date.fromisoformat(request.GET.get('data_inicio') or hoje.replace(day=1).isoformat())
    data_fim = date.fromisoformat(request.GET.get('data_fim') or hoje.isoformat())

    funcionario_id = request.GET.get('funcionario')
    funcionario = None
    resultado = None
    valores = None
    jornada_vigente = None
    numeros_dos_pares = []

    if funcionario_id:
        funcionario = get_object_or_404(Funcionario, id=funcionario_id)
        resultado = calcular_periodo(funcionario, data_inicio, data_fim)
        valores = valores_do_periodo(resultado['totais'], funcionario.salario)
        jornada_vigente = FuncionarioJornada.vigente_em(funcionario, data_fim)
        numeros_dos_pares = list(range(1, resultado['pares_no_periodo'] + 1))

    return render(request, 'ponto/calculo.html', {
        'funcionarios': Funcionario.objects.order_by('nome'),
        'funcionario': funcionario,
        'funcionario_id': funcionario_id or '',
        'data_inicio': data_inicio.isoformat(),
        'data_fim': data_fim.isoformat(),
        'resultado': resultado,
        'valores': valores,
        'jornada_vigente': jornada_vigente,
        'jornada_form': FuncionarioJornadaForm(),
        'numeros_dos_pares': numeros_dos_pares,
    })


@login_required
@papel_required('rh', 'dp')
def atribuir_jornada_view(request, funcionario_id):
    funcionario = get_object_or_404(Funcionario, id=funcionario_id)

    if request.method == 'POST':
        form = FuncionarioJornadaForm(request.POST)
        if form.is_valid():
            data_inicio = form.cleaned_data['data_inicio']
            horario = form.cleaned_data['horario']
            data_formatada = data_inicio.strftime('%d/%m/%Y')
            # Só pode haver uma jornada por funcionário por data (constraint do
            # model). Mandar a mesma data de novo é o jeito de corrigir um horário
            # escolhido errado: troca o horário daquela jornada em vez de dar erro.
            # `get_or_create` também segura o clique duplo no botão.
            jornada, criada = FuncionarioJornada.objects.get_or_create(
                funcionario=funcionario, data_inicio=data_inicio, defaults={'horario': horario},
            )
            if criada:
                messages.success(request, f'Jornada de {funcionario.nome} definida a partir de {data_formatada}: horário {horario}.')
            elif jornada.horario_id == horario.id:
                messages.success(request, f'A jornada de {funcionario.nome} a partir de {data_formatada} já era o horário {horario}. Nada mudou.')
            else:
                anterior = jornada.horario
                jornada.horario = horario
                jornada.save(update_fields=['horario'])
                messages.success(
                    request,
                    f'Jornada de {funcionario.nome} a partir de {data_formatada}: horário trocado de {anterior} para {horario}.',
                )
        else:
            messages.error(request, 'Não foi possível salvar: confira o horário e a data informados.')

    proxima = request.POST.get('proxima')
    if proxima and url_has_allowed_host_and_scheme(proxima, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(proxima)

    url = f"{reverse('ponto:listar_calculo')}?funcionario={funcionario.id}"
    data_inicio = request.POST.get('data_inicio')
    data_fim = request.POST.get('data_fim')
    if data_inicio and data_fim:
        url += f'&data_inicio={data_inicio}&data_fim={data_fim}'

    return redirect(url)


@login_required
@papel_required('rh', 'dp')
def listar_feriados_view(request):
    return render(request, 'ponto/listar_feriados.html', {'feriados': Feriado.objects.all()})


@login_required
@papel_required('rh', 'dp')
def cadastrar_feriado_view(request):
    if request.method == 'POST':
        form = FeriadoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Feriado cadastrado com sucesso.')
            return redirect('ponto:listar_horarios')
    else:
        form = FeriadoForm()

    return render(request, 'ponto/form_feriado.html', {'form': form})


@login_required
@papel_required('rh', 'dp')
def excluir_feriado_view(request, feriado_id):
    feriado = get_object_or_404(Feriado, id=feriado_id)
    feriado.delete()
    messages.success(request, 'Feriado excluído.')
    return redirect('ponto:listar_horarios')


# ---------- Colaborador: Meu Ponto ----------
# Sem @papel_required de propósito -- é autoatendimento igual meu_perfil_view/
# solicitar_ferias_view em funcionarios/views.py, disponível pra qualquer
# Usuario que bate ponto (RH e DP também batem). Gestor não: todo gerente tem
# cargo de confiança e não registra ponto, então ele cai no ponto da equipe.

def _eh_gestor(request):
    try:
        return request.user.usuario.papel == 'gestor'
    except Usuario.DoesNotExist:
        return False


def _periodo_meu_ponto(request):
    hoje = timezone.now().date()
    data_fim = min(_data_do_get(request, 'data_fim', hoje), hoje)
    data_inicio = _data_do_get(request, 'data_inicio', data_fim - timedelta(days=JANELA_MEU_PONTO_DIAS - 1))
    if data_inicio > data_fim:
        data_inicio = data_fim - timedelta(days=JANELA_MEU_PONTO_DIAS - 1)
    if (data_fim - data_inicio).days > JANELA_MEU_PONTO_MAXIMA_DIAS:
        data_inicio = data_fim - timedelta(days=JANELA_MEU_PONTO_MAXIMA_DIAS)
    return hoje, data_inicio, data_fim


def _navegacao_periodo(data_inicio, data_fim, hoje):
    periodo_anterior = {
        'data_inicio': (data_inicio - timedelta(days=JANELA_MEU_PONTO_DIAS)).isoformat(),
        'data_fim': (data_inicio - timedelta(days=1)).isoformat(),
    }
    periodo_seguinte = None
    if data_fim < hoje:
        nova_data_inicio = data_fim + timedelta(days=1)
        periodo_seguinte = {
            'data_inicio': nova_data_inicio.isoformat(),
            'data_fim': min(nova_data_inicio + timedelta(days=JANELA_MEU_PONTO_DIAS - 1), hoje).isoformat(),
        }
    return {'periodo_anterior': periodo_anterior, 'periodo_seguinte': periodo_seguinte}


@login_required
def meu_ponto_view(request):
    if _eh_gestor(request):
        return redirect('ponto:ponto_equipe')
    try:
        funcionario = request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return render(request, 'funcionarios/sem_perfil.html')

    hoje, data_inicio, data_fim = _periodo_meu_ponto(request)
    resultado = calcular_periodo(funcionario, data_inicio, data_fim)
    for dia in resultado['dias']:
        texto, cor, pode_solicitar = situacao_do_dia(dia, hoje)
        dia['situacao_texto'] = texto
        dia['situacao_cor'] = cor
        dia['pode_solicitar'] = pode_solicitar
        dia['tipo_sugerido'] = 'saida' if dia['marcacao_impar'] else 'entrada'

    return render(request, 'ponto/meu_ponto.html', {
        'funcionario': funcionario,
        'resultado': resultado,
        'data_inicio': data_inicio,
        'data_fim': data_fim,
        **_navegacao_periodo(data_inicio, data_fim, hoje),
        'numeros_dos_pares': list(range(1, resultado['pares_no_periodo'] + 1)),
    })


@login_required
def solicitar_ajuste_ponto_view(request):
    if _eh_gestor(request):
        return redirect('ponto:ponto_equipe')
    try:
        funcionario = request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return render(request, 'funcionarios/sem_perfil.html')

    if request.method == 'POST':
        form = SolicitarAjusteForm(request.POST, funcionario=funcionario)
        if form.is_valid():
            ajuste = form.save(commit=False)
            ajuste.funcionario = funcionario
            ajuste.usuario = request.user
            ajuste.solicitado_por = request.user
            ajuste.status = 'pendente'
            ajuste.save()
            notificacoes.ajuste_ponto_solicitado(ajuste)
            messages.success(request, 'Solicitação enviada. Acompanhe em "Meus Ajustes" -- o RH vai analisar.')
            return redirect('ponto:meus_ajustes')
    else:
        inicial = {
            'data': request.GET.get('data') or timezone.now().date().isoformat(),
            'tipo_evento': request.GET.get('tipo') or 'entrada',
        }
        marcacao_original_id = request.GET.get('marcacao_original')
        if marcacao_original_id:
            inicial['marcacao_original'] = marcacao_original_id
        form = SolicitarAjusteForm(initial=inicial, funcionario=funcionario)

    return render(request, 'ponto/form_solicitar_ajuste.html', {'form': form, 'funcionario': funcionario})


@login_required
def meus_ajustes_view(request):
    if _eh_gestor(request):
        return redirect('ponto:ponto_equipe')
    try:
        funcionario = request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return render(request, 'funcionarios/sem_perfil.html')

    ajustes = AjustePonto.objects.filter(funcionario=funcionario).select_related('analisado_por')
    return render(request, 'ponto/meus_ajustes.html', {'ajustes': ajustes})


# ---------- Gestor: ponto da equipe ----------
# Gestor não bate ponto (cargo de confiança) -- no lugar do "Meu Ponto" ele vê
# as batidas de quem trabalha nos setores dele. Só leitura: correção continua
# sendo pedida pelo próprio colaborador e analisada pelo RH.

def _funcionarios_da_equipe(usuario):
    """Setor do próprio gestor + setores em que ele é o responsável, sem outros gestores (que também não batem ponto)."""
    gestor = usuario.funcionario
    return (
        Funcionario.objects
        .filter(Q(setor_id=gestor.setor_id) | Q(setor__gestor=gestor), status='ativo')
        .exclude(id=gestor.id)
        .exclude(usuario__papel='gestor')
        .select_related('setor')
        .order_by('nome')
    )


@login_required
@papel_required('gestor')
def ponto_equipe_view(request):
    hoje = timezone.now().date()
    data = min(_data_do_get(request, 'data', hoje), hoje)

    linhas = []
    for funcionario in _funcionarios_da_equipe(request.user.usuario):
        dia = calcular_periodo(funcionario, data, data)['dias'][0]
        texto, cor, _ = situacao_do_dia(dia, hoje)
        linhas.append({'funcionario': funcionario, 'dia': dia, 'situacao_texto': texto, 'situacao_cor': cor})

    return render(request, 'ponto/ponto_equipe.html', {
        'data': data,
        'linhas': linhas,
        'dia_anterior': (data - timedelta(days=1)).isoformat(),
        'dia_seguinte': (data + timedelta(days=1)).isoformat() if data < hoje else None,
        'eh_hoje': data == hoje,
    })


@login_required
@papel_required('gestor')
def ponto_equipe_funcionario_view(request, funcionario_id):
    funcionario = get_object_or_404(_funcionarios_da_equipe(request.user.usuario), id=funcionario_id)

    hoje, data_inicio, data_fim = _periodo_meu_ponto(request)
    resultado = calcular_periodo(funcionario, data_inicio, data_fim)
    for dia in resultado['dias']:
        dia['situacao_texto'], dia['situacao_cor'], _ = situacao_do_dia(dia, hoje)

    return render(request, 'ponto/ponto_equipe_funcionario.html', {
        'funcionario': funcionario,
        'resultado': resultado,
        'data_inicio': data_inicio,
        'data_fim': data_fim,
        **_navegacao_periodo(data_inicio, data_fim, hoje),
    })


# ---------- RH: fila de aprovação de ajustes ----------

@login_required
@papel_required('rh', 'dp')
def ajustes_pendentes_view(request):
    ajustes = (
        AjustePonto.objects
        .filter(status='pendente')
        .select_related('funcionario', 'solicitado_por')
        .order_by('criado_em')
    )
    return render(request, 'ponto/ajustes_pendentes.html', {
        'ajustes': ajustes,
        'form_rejeicao': RejeitarAjusteForm(),
    })


@login_required
@papel_required('rh', 'dp')
def aprovar_ajuste_view(request, ajuste_id):
    ajuste = get_object_or_404(AjustePonto, id=ajuste_id)

    if ajuste.status != 'pendente':
        messages.error(request, 'Este ajuste já foi analisado.')
        return redirect('ponto:ajustes_pendentes')

    ja_existe_aprovado = AjustePonto.objects.filter(
        funcionario=ajuste.funcionario, data=ajuste.data, tipo_evento=ajuste.tipo_evento, status='aprovado',
    ).exists()
    if ja_existe_aprovado:
        messages.error(
            request,
            f'Já existe um ajuste aprovado pra {ajuste.funcionario.nome} em {ajuste.data.strftime("%d/%m/%Y")} '
            f'({ajuste.get_tipo_evento_display()}). Rejeite este pedido ou resolva a duplicidade antes de aprovar.',
        )
        return redirect('ponto:ajustes_pendentes')

    ajuste.status = 'aprovado'
    ajuste.analisado_por = request.user
    ajuste.analisado_em = timezone.now()
    ajuste.save()
    notificacoes.ajuste_ponto_analisado(ajuste)
    messages.success(request, f'Ajuste de {ajuste.funcionario.nome} aprovado.')
    return redirect('ponto:ajustes_pendentes')


@login_required
@papel_required('rh', 'dp')
def rejeitar_ajuste_view(request, ajuste_id):
    ajuste = get_object_or_404(AjustePonto, id=ajuste_id)

    if ajuste.status != 'pendente':
        messages.error(request, 'Este ajuste já foi analisado.')
        return redirect('ponto:ajustes_pendentes')

    form = RejeitarAjusteForm(request.POST)
    if form.is_valid():
        ajuste.status = 'rejeitado'
        ajuste.observacao_rh = form.cleaned_data['observacao_rh']
        ajuste.analisado_por = request.user
        ajuste.analisado_em = timezone.now()
        ajuste.save()
        notificacoes.ajuste_ponto_analisado(ajuste)
        messages.success(request, f'Ajuste de {ajuste.funcionario.nome} rejeitado.')
    else:
        messages.error(request, 'Informe o motivo da rejeição.')

    return redirect('ponto:ajustes_pendentes')
