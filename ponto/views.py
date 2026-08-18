from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from funcionarios.decorators import papel_required
from funcionarios.models import Funcionario

from . import services
from .calculos import calcular_periodo
from .forms import (
    DIAS_SEMANA_INICIAIS, EquipamentoForm, EscalaForm, FeriadoForm, FuncionarioJornadaForm,
    HorarioDiaFormSet, HorarioForm, escala_item_formset_factory,
)
from .models import Equipamento, Escala, Feriado, FuncionarioJornada, Horario, HorarioDia, MarcacaoRelogio
from .utils import intervalo_do_dia


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
@papel_required('rh')
def listar_equipamentos_view(request):
    equipamentos = Equipamento.objects.all()
    return render(request, 'ponto/listar_equipamentos.html', {
        'equipamentos': equipamentos,
    })


@login_required
@papel_required('rh')
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
@papel_required('rh')
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
@papel_required('rh')
def testar_conexao_equipamento_view(request, equipamento_id):
    equipamento = get_object_or_404(Equipamento, id=equipamento_id)

    ok, erro = services.testar_conexao(equipamento)
    if ok:
        messages.success(request, f'Conexão com "{equipamento.nome}" funcionando normalmente.')
    else:
        messages.error(request, f'Não foi possível conectar a "{equipamento.nome}": {erro}')

    return redirect('ponto:listar_equipamentos')


@login_required
@papel_required('rh')
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
@papel_required('rh')
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
@papel_required('rh')
def sincronizar_equipamento_view(request, equipamento_id):
    """
    Botão único "Sincronizar": envia cadastros pendentes e puxa marcações
    novas num clique só -- sem detalhe por funcionário na tela, só o
    resultado resumido.
    """
    equipamento = get_object_or_404(Equipamento, id=equipamento_id)
    processadas, sucesso, marcacoes_novas = services.sincronizar_equipamento_completo(equipamento)

    resumo = f'{marcacoes_novas} marcação(ões) nova(s)'
    if processadas:
        resumo += f', {sucesso}/{processadas} cadastro(s) enviado(s)'

    if processadas and sucesso < processadas:
        messages.error(request, f'"{equipamento.nome}": {resumo}. Cadastros com falha serão tentados de novo automaticamente.')
    else:
        messages.success(request, f'"{equipamento.nome}": {resumo}.')

    return redirect('ponto:listar_equipamentos')


@login_required
@papel_required('rh')
def sincronizar_todos_view(request):
    total_marcacoes = 0
    total_processadas = 0
    total_sucesso = 0

    for equipamento in Equipamento.objects.filter(ativo=True):
        processadas, sucesso, marcacoes_novas = services.sincronizar_equipamento_completo(equipamento)
        total_marcacoes += marcacoes_novas
        total_processadas += processadas
        total_sucesso += sucesso

    resumo = f'{total_marcacoes} marcação(ões) nova(s)'
    if total_processadas:
        resumo += f', {total_sucesso}/{total_processadas} cadastro(s) enviado(s)'

    if total_processadas and total_sucesso < total_processadas:
        messages.error(request, f'{resumo}. Alguns cadastros ficaram em erro e serão tentados de novo automaticamente.')
    else:
        messages.success(request, resumo + '.')

    return redirect('ponto:listar_equipamentos')


@login_required
@papel_required('rh')
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
@papel_required('rh')
def listar_horarios_view(request):
    horarios = Horario.objects.prefetch_related('dias')
    return render(request, 'ponto/listar_horarios.html', {'horarios': horarios})


@login_required
@papel_required('rh')
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
@papel_required('rh')
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

    return render(request, 'ponto/form_horario.html', {
        'form': form, 'formset': formset, 'horario': horario, 'dias_com_forms': _dias_com_forms(formset),
    })


@login_required
@papel_required('rh')
def excluir_horario_view(request, horario_id):
    horario = get_object_or_404(Horario, id=horario_id)
    numero = horario.numero
    horario.delete()
    messages.success(request, f'Horário {numero} excluído.')
    return redirect('ponto:listar_horarios')


@login_required
@papel_required('rh')
def listar_escalas_view(request):
    escalas = Escala.objects.prefetch_related('itens__horario')
    return render(request, 'ponto/listar_escalas.html', {'escalas': escalas})


@login_required
@papel_required('rh')
def cadastrar_escala_view(request):
    if request.method == 'POST':
        form = EscalaForm(request.POST)
        if form.is_valid():
            escala = form.save()
            for posicao in range(1, escala.tamanho_ciclo + 1):
                escala.itens.create(posicao=posicao)
            messages.success(
                request,
                f'Escala "{escala.descricao}" cadastrada. Agora defina o horário (ou folga) de cada posição do ciclo.',
            )
            return redirect('ponto:editar_escala', escala.id)
    else:
        form = EscalaForm()

    return render(request, 'ponto/form_escala.html', {'form': form, 'escala': None, 'formset': None})


@login_required
@papel_required('rh')
def editar_escala_view(request, escala_id):
    escala = get_object_or_404(Escala, id=escala_id)
    EscalaItemFormSet = escala_item_formset_factory(escala)

    if request.method == 'POST':
        form = EscalaForm(request.POST, instance=escala)
        formset = EscalaItemFormSet(request.POST, instance=escala)
        if form.is_valid() and formset.is_valid():
            form.save()
            formset.save()
            messages.success(request, f'Escala "{escala.descricao}" atualizada com sucesso.')
            return redirect('ponto:listar_escalas')
    else:
        form = EscalaForm(instance=escala)
        formset = EscalaItemFormSet(instance=escala)

    return render(request, 'ponto/form_escala.html', {'form': form, 'formset': formset, 'escala': escala})


@login_required
@papel_required('rh')
def excluir_escala_view(request, escala_id):
    escala = get_object_or_404(Escala, id=escala_id)
    descricao = escala.descricao
    escala.delete()
    messages.success(request, f'Escala "{descricao}" excluída.')
    return redirect('ponto:listar_escalas')


@login_required
@papel_required('rh')
def listar_calculo_view(request):
    hoje = timezone.now().date()
    data_inicio = date.fromisoformat(request.GET.get('data_inicio') or hoje.replace(day=1).isoformat())
    data_fim = date.fromisoformat(request.GET.get('data_fim') or hoje.isoformat())

    funcionario_id = request.GET.get('funcionario')
    funcionario = None
    resultado = None
    jornada_vigente = None
    numeros_dos_pares = []

    if funcionario_id:
        funcionario = get_object_or_404(Funcionario, id=funcionario_id)
        resultado = calcular_periodo(funcionario, data_inicio, data_fim)
        jornada_vigente = FuncionarioJornada.vigente_em(funcionario, data_fim)
        numeros_dos_pares = list(range(1, resultado['pares_no_periodo'] + 1))

    return render(request, 'ponto/calculo.html', {
        'funcionarios': Funcionario.objects.order_by('nome'),
        'funcionario': funcionario,
        'funcionario_id': funcionario_id or '',
        'data_inicio': data_inicio.isoformat(),
        'data_fim': data_fim.isoformat(),
        'resultado': resultado,
        'jornada_vigente': jornada_vigente,
        'jornada_form': FuncionarioJornadaForm(),
        'numeros_dos_pares': numeros_dos_pares,
    })


@login_required
@papel_required('rh')
def atribuir_jornada_view(request, funcionario_id):
    funcionario = get_object_or_404(Funcionario, id=funcionario_id)

    if request.method == 'POST':
        form = FuncionarioJornadaForm(request.POST)
        if form.is_valid():
            jornada = form.save(commit=False)
            jornada.funcionario = funcionario
            jornada.save()
            messages.success(request, f'Jornada de {funcionario.nome} definida a partir de {jornada.data_inicio}.')
        else:
            messages.error(request, 'Não foi possível salvar: escolha um horário fixo OU uma escala.')

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
@papel_required('rh')
def listar_feriados_view(request):
    return render(request, 'ponto/listar_feriados.html', {'feriados': Feriado.objects.all()})


@login_required
@papel_required('rh')
def cadastrar_feriado_view(request):
    if request.method == 'POST':
        form = FeriadoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'Feriado cadastrado com sucesso.')
            return redirect('ponto:listar_feriados')
    else:
        form = FeriadoForm()

    return render(request, 'ponto/form_feriado.html', {'form': form})


@login_required
@papel_required('rh')
def excluir_feriado_view(request, feriado_id):
    feriado = get_object_or_404(Feriado, id=feriado_id)
    feriado.delete()
    messages.success(request, 'Feriado excluído.')
    return redirect('ponto:listar_feriados')
