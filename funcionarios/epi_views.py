"""
Módulo de EPI (Segurança do Trabalho).

Segue o mesmo desenho do comodato (`views.py`): ficha de entrega com itens,
PDF gerado, colaborador devolve assinado em Meus Documentos e quem opera o
módulo valida. Acrescenta a matriz de EPI obrigatório por setor, o alerta de
troca e o registro de não conformidades.
"""

import io
import os
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Case, Count, IntegerField, Prefetch, Q, Value, When
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.views.decorators.http import require_POST
from xhtml2pdf import pisa

from . import notificacoes
from .arquivos import resposta_de_arquivo, validar_upload
from .decorators import papel_required
from .forms import (
    ConfirmarTrocaEPIForm, FichaEPIForm, ItemCatalogoEPIForm, OcorrenciaEPIForm, RejeitarDocumentoForm,
    SolicitacaoTrocaEPIForm, item_epi_formset,
)
from .models import (
    FichaEPI, Funcionario, ItemCatalogoEPI, ItemEPI, MatrizEpiSetor, OcorrenciaEPI, Setor, SolicitacaoTrocaEPI,
    Usuario,
)
from .utils import data_por_extenso, formatar_cpf


LIMITE_DIAS_ALERTA_EPI = 30
LINHAS_MINIMAS_FICHA = 5


def _funcionarios_busca(queryset):
    return [
        {
            'id': f.id, 'nome': f.nome, 'matricula': f.matricula or '', 'cpf': f.cpf or '',
            'setor_id': f.setor_id, 'setor': f.setor.nome if f.setor else '',
        }
        for f in queryset.select_related('setor').only('id', 'nome', 'matricula', 'cpf', 'setor__nome')
    ]


def _itens_epi_vencendo():
    """EPIs entregues e não devolvidos com troca vencida ou nos próximos LIMITE_DIAS_ALERTA_EPI dias."""
    limite = timezone.now().date() + timedelta(days=LIMITE_DIAS_ALERTA_EPI)
    # Desligado não troca EPI, devolve: esses aparecem em "Devolução pendente".
    return ItemEPI.objects.filter(
        data_vencimento__isnull=False, data_vencimento__lte=limite, devolvido=False,
    ).exclude(ficha__funcionario__status='inativo')


def _total_pendencias_epi():
    return _itens_epi_vencendo().count() + FichaEPI.com_devolucao_pendente().count()


# ---------- Fichas de entrega ----------

@login_required
@papel_required('seguranca_trabalho', 'rh')
def listar_fichas_epi_view(request):
    # As que esperam validação vêm primeiro: é a fila de trabalho.
    fichas = FichaEPI.objects.select_related('funcionario__setor').annotate(
        aguardando_validacao=Case(When(status='em_validacao', then=Value(0)), default=Value(1), output_field=IntegerField()),
        total_itens=Count('itens'),
    ).order_by('aguardando_validacao', '-gerado_em')

    busca = request.GET.get('busca', '').strip()
    if busca:
        fichas = fichas.filter(Q(funcionario__nome__icontains=busca) | Q(funcionario__setor__nome__icontains=busca))

    return render(request, 'funcionarios/epi/listar_fichas.html', {
        'busca': busca,
        'fichas': fichas,
        'abrir_pendente': request.GET.get('pendentes') == '1',
        'total_pendencias_epi': _total_pendencias_epi(),
    })


@login_required
@papel_required('seguranca_trabalho', 'rh')
def criar_ficha_epi_view(request):
    # Só itens ativos: um EPI desativado no catálogo não aparece no select da linha.
    matriz = {}
    for linha in MatrizEpiSetor.objects.filter(item_catalogo__ativo=True).order_by('setor_id', 'item_catalogo__nome'):
        matriz.setdefault(str(linha.setor_id), []).append({
            'item': str(linha.item_catalogo_id),
            'quantidade': linha.quantidade_padrao,
        })
    maior_setor = max((len(itens) for itens in matriz.values()), default=0)
    ItemEPIFormSet = item_epi_formset(extra=max(LINHAS_MINIMAS_FICHA, maior_setor))

    if request.method == 'POST':
        form = FichaEPIForm(request.POST)
        formset = ItemEPIFormSet(request.POST)

        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                ficha = form.save(commit=False)
                ficha.gerado_por = request.user
                ficha.save()
                formset.instance = ficha
                formset.save()

            # Já gera o PDF: a ficha só aparece em Meus Documentos depois disso.
            if _gerar_pdf_ficha(ficha):
                messages.success(request, 'Ficha de EPI criada e disponibilizada ao colaborador para assinatura.')
            else:
                messages.warning(request, 'Ficha criada, mas o PDF não pôde ser gerado. Use "Gerar PDF" na lista.')
            return redirect('listar_fichas_epi')
    else:
        form = FichaEPIForm(initial={'funcionario': _funcionario_pedido(request)})
        formset = ItemEPIFormSet()

    return render(request, 'funcionarios/epi/criar_ficha.html', {
        'form': form,
        'formset': formset,
        'funcionarios_busca': _funcionarios_busca(form.fields['funcionario'].queryset),
        'matriz_por_setor': matriz,
    })


def _gerar_pdf_ficha(ficha):
    """Gera o PDF da ficha e a deixa aguardando a assinatura do colaborador. Devolve False se o PDF falhar."""
    contexto = {
        'funcionario': ficha.funcionario,
        'cpf_formatado': formatar_cpf(ficha.funcionario.cpf),
        'itens': ficha.itens.select_related('item_catalogo'),
        # Data do documento: o dia em que é gerado (a entrega só é registrada na validação).
        'data_extenso': data_por_extenso(ficha.data_entrega or timezone.localdate()),
        'caminho_logo': os.path.join(settings.BASE_DIR, 'static', 'img', 'logo-sotrigo.png'),
    }
    html = render_to_string('funcionarios/epi/ficha_epi.html', contexto)

    buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(html, dest=buffer)
    if pisa_status.err:
        return False

    nome_arquivo = f'ficha_epi_{ficha.id}_{ficha.funcionario.cpf}.pdf'
    ficha.arquivo_gerado.save(nome_arquivo, ContentFile(buffer.getvalue()), save=False)
    ficha.status = 'aguardando_assinatura'
    ficha.save()
    return True


@login_required
@papel_required('seguranca_trabalho', 'rh')
@require_POST
def gerar_pdf_ficha_epi_view(request, ficha_id):
    ficha = get_object_or_404(FichaEPI.objects.select_related('funcionario'), id=ficha_id)

    if _gerar_pdf_ficha(ficha):
        messages.success(request, 'Ficha de EPI gerada e disponibilizada ao colaborador.')
    else:
        messages.error(request, 'Não foi possível gerar a ficha. Tente novamente.')
    return redirect('listar_fichas_epi')


@login_required
@xframe_options_sameorigin  # a lista de fichas exibe o PDF num iframe, dentro do próprio sistema
def ver_ficha_epi_view(request, ficha_id, versao):
    """Serve a ficha (gerada ou assinada) só para o próprio colaborador ou quem opera o módulo."""
    if versao not in ('gerado', 'assinado'):
        raise Http404
    ficha = get_object_or_404(FichaEPI, id=ficha_id)

    try:
        usuario = request.user.usuario
    except Usuario.DoesNotExist:
        messages.error(request, 'Você não tem permissão para acessar esta ficha.')
        return redirect('dashboard')

    if not (usuario.funcionario_id == ficha.funcionario_id or usuario.eh_seguranca_ou_rh()):
        messages.error(request, 'Você não tem permissão para acessar esta ficha.')
        return redirect('dashboard')

    arquivo = ficha.arquivo_assinado if versao == 'assinado' else ficha.arquivo_gerado
    if not arquivo:
        raise Http404('Ficha não disponível.')

    return resposta_de_arquivo(arquivo)


# Sem @papel_required de propósito: quem envia é o próprio colaborador, e a
# posse da ficha é garantida pelo filtro por funcionário no get_object_or_404.
@login_required
def reenviar_epi_assinado_view(request, ficha_id):
    try:
        funcionario = request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return redirect('dashboard')
    ficha = get_object_or_404(FichaEPI, id=ficha_id, funcionario=funcionario)
    destino = 'minhas_assinaturas' if request.POST.get('origem') == 'assinatura_gov' else 'meus_documentos'

    if ficha.status != 'aguardando_assinatura' or not ficha.arquivo_gerado:
        messages.error(request, 'Esta ficha não está aguardando assinatura.')
        return redirect(destino)

    if request.method == 'POST' and request.FILES.get('arquivo_assinado'):
        try:
            arquivo = validar_upload(request.FILES['arquivo_assinado'])
        except ValidationError as erro:
            messages.error(request, f'Ficha não enviada. {erro.messages[0]}')
            return redirect(destino)
        ficha.arquivo_assinado = arquivo
        ficha.status = 'em_validacao'
        ficha.motivo_rejeicao = ''
        ficha.save()
        notificacoes.ficha_epi_assinada_enviada(ficha)
        messages.success(request, 'Ficha de EPI assinada enviada. Aguarde a validação da Segurança do Trabalho.')

    return redirect(destino)


def _voltar_apos_validar_ficha(request):
    destino = request.POST.get('next', '')
    if url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(destino)
    return redirect('listar_fichas_epi')


@login_required
@papel_required('seguranca_trabalho', 'rh')
def aprovar_ficha_epi_view(request, ficha_id):
    ficha = get_object_or_404(FichaEPI, id=ficha_id)

    if request.method != 'POST' or ficha.status != 'em_validacao':
        messages.error(request, 'Esta ficha não está aguardando validação.')
        return _voltar_apos_validar_ficha(request)

    ficha.status = 'ativo'
    ficha.motivo_rejeicao = ''
    ficha.validado_por = request.user
    ficha.validado_em = timezone.now()
    # A entrega vale a partir da assinatura validada.
    ficha.data_entrega = timezone.localdate()
    ficha.save()

    messages.success(request, f'Ficha de EPI de {ficha.funcionario.nome} validada.')
    return _voltar_apos_validar_ficha(request)


@login_required
@papel_required('seguranca_trabalho', 'rh')
def rejeitar_ficha_epi_view(request, ficha_id):
    ficha = get_object_or_404(FichaEPI, id=ficha_id)

    if request.method != 'POST' or ficha.status != 'em_validacao':
        messages.error(request, 'Esta ficha não está aguardando validação.')
        return _voltar_apos_validar_ficha(request)

    form = RejeitarDocumentoForm(request.POST)
    if form.is_valid():
        # Volta para o colaborador assinar de novo; a ficha gerada continua a mesma.
        ficha.status = 'aguardando_assinatura'
        ficha.motivo_rejeicao = form.cleaned_data['motivo']
        ficha.validado_por = request.user
        ficha.validado_em = timezone.now()
        ficha.save()
        messages.success(request, 'Ficha rejeitada. O colaborador poderá reenviar.')
    else:
        messages.error(request, 'Informe o motivo da rejeição.')

    return _voltar_apos_validar_ficha(request)


# ---------- Troca e devolução ----------

@login_required
@papel_required('seguranca_trabalho', 'rh')
def listar_epis_vencendo_view(request):
    hoje = timezone.now().date()
    itens = list(_itens_epi_vencendo().select_related('ficha__funcionario', 'item_catalogo').order_by('data_vencimento'))

    for item in itens:
        item.dias = (item.data_vencimento - hoje).days
        item.dias_abs = abs(item.dias)

    devolucoes = list(
        FichaEPI.com_devolucao_pendente().select_related('funcionario').prefetch_related(
            Prefetch('itens', queryset=ItemEPI.objects.filter(devolvido=False).select_related('item_catalogo'),
                     to_attr='itens_pendentes')
        ).order_by('funcionario__nome', 'data_entrega')
    )

    avisos_troca = ItemEPI.com_aviso_de_troca().select_related('ficha__funcionario', 'item_catalogo').order_by(
        'data_vencimento', 'ficha__funcionario__nome',
    )

    return render(request, 'funcionarios/epi/epis_vencendo.html', {
        'avisos_troca': avisos_troca,
        'itens': itens,
        'devolucoes': devolucoes,
        'total_pendencias_epi': len(itens) + len(devolucoes),
        'hoje': hoje,
        'limite_dias': LIMITE_DIAS_ALERTA_EPI,
    })


@login_required
@papel_required('seguranca_trabalho', 'rh')
def registrar_devolucao_epi_view(request, ficha_id):
    ficha = get_object_or_404(FichaEPI, id=ficha_id)
    if request.method != 'POST':
        return redirect('listar_epis_vencendo')

    itens = list(ficha.itens.filter(devolvido=False, id__in=request.POST.getlist('itens')).select_related('item_catalogo'))
    if not itens:
        messages.error(request, 'Marque ao menos um EPI devolvido.')
        return redirect('listar_epis_vencendo')

    ficha.registrar_devolucao(itens, request.POST.get('observacoes', '').strip())
    if ficha.status == 'devolvido_total':
        messages.success(request, f'Devolução de {ficha.funcionario.nome} registrada. Todos os EPIs foram devolvidos.')
    else:
        messages.warning(request, f'Devolução parcial de {ficha.funcionario.nome} registrada. Ainda há EPI a devolver.')
    return redirect('listar_epis_vencendo')


# ---------- Catálogo de EPIs ----------

@login_required
@papel_required('seguranca_trabalho', 'rh')
def listar_catalogo_epi_view(request):
    hoje = timezone.now().date()
    limite = hoje + timedelta(days=LIMITE_DIAS_ALERTA_EPI)
    itens = list(ItemCatalogoEPI.objects.annotate(
        inativo=Case(When(ativo=True, then=Value(0)), default=Value(1), output_field=IntegerField()),
        total_setores=Count('matriz_setores', distinct=True),
    ).order_by('inativo', 'nome'))

    for item in itens:
        if item.validade_ca and item.validade_ca < hoje:
            item.situacao_ca = 'vencido'
        elif item.validade_ca and item.validade_ca <= limite:
            item.situacao_ca = 'vencendo'
        else:
            item.situacao_ca = ''

    return render(request, 'funcionarios/epi/catalogo.html', {
        'itens': itens,
        'total_ativos': sum(1 for item in itens if item.ativo),
        'limite_dias': LIMITE_DIAS_ALERTA_EPI,
    })


def _form_catalogo_epi(request, item=None):
    form = ItemCatalogoEPIForm(request.POST or None, instance=item)
    if request.method == 'POST' and form.is_valid():
        salvo = form.save()
        messages.success(request, f'EPI "{salvo.nome}" {"atualizado" if item else "cadastrado"}.')
        return redirect('listar_catalogo_epi')

    return render(request, 'funcionarios/epi/form_catalogo.html', {
        'form': form,
        'item': item,
        'pode_excluir': item is not None and not item.em_uso(),
    })


@login_required
@papel_required('seguranca_trabalho', 'rh')
def cadastrar_epi_catalogo_view(request):
    return _form_catalogo_epi(request)


@login_required
@papel_required('seguranca_trabalho', 'rh')
def editar_epi_catalogo_view(request, item_id):
    return _form_catalogo_epi(request, get_object_or_404(ItemCatalogoEPI, id=item_id))


@login_required
@papel_required('seguranca_trabalho', 'rh')
@require_POST
def excluir_epi_catalogo_view(request, item_id):
    item = get_object_or_404(ItemCatalogoEPI, id=item_id)
    if item.em_uso():
        messages.error(request, f'"{item.nome}" já foi usado em ficha, matriz ou ocorrência. Inative-o em vez de excluir.')
        return redirect('editar_epi_catalogo', item_id=item.id)

    item.delete()
    messages.success(request, f'EPI "{item.nome}" excluído do catálogo.')
    return redirect('listar_catalogo_epi')


# ---------- Matriz de EPI por setor ----------

@login_required
@papel_required('seguranca_trabalho', 'rh')
def gerenciar_matriz_epi_view(request):
    setores = list(Setor.objects.annotate(total=Count('matriz_epi')).order_by('nome'))

    setor_id = request.POST.get('setor') if request.method == 'POST' else request.GET.get('setor', '')
    setor = None
    if setor_id:
        setor = Setor.objects.filter(id=setor_id).first() if setor_id.isdigit() else None
        if setor is None:
            messages.error(request, 'Setor não encontrado.')
            return redirect('gerenciar_matriz_epi')

    catalogo = ItemCatalogoEPI.objects.filter(ativo=True)

    if request.method == 'POST' and setor:
        marcados = {}
        for item in catalogo:
            if request.POST.get(f'item_{item.id}'):
                try:
                    quantidade = max(1, int(request.POST.get(f'quantidade_{item.id}') or 1))
                except ValueError:
                    quantidade = 1
                marcados[item.id] = quantidade

        with transaction.atomic():
            # Só mexe nos itens ativos: os desativados ficam como estão.
            MatrizEpiSetor.objects.filter(setor=setor, item_catalogo__in=catalogo).exclude(
                item_catalogo_id__in=marcados
            ).delete()
            for item_id, quantidade in marcados.items():
                MatrizEpiSetor.objects.update_or_create(
                    setor=setor, item_catalogo_id=item_id, defaults={'quantidade_padrao': quantidade},
                )

        messages.success(request, f'Matriz do setor "{setor.nome}" atualizada.')
        # Fecha a edição: volta para as caixinhas das matrizes criadas.
        return redirect('gerenciar_matriz_epi')

    linhas_catalogo = []
    if setor:
        atuais = dict(setor.matriz_epi.values_list('item_catalogo_id', 'quantidade_padrao'))
        linhas_catalogo = [
            {'item': item, 'marcado': item.id in atuais, 'quantidade': atuais.get(item.id, 1)}
            for item in catalogo
        ]

    # Matrizes já montadas, em caixinhas: clicar abre a edição do setor.
    matrizes = [s for s in setores if s.total]
    itens_por_setor = {}
    for linha in MatrizEpiSetor.objects.filter(setor__in=matrizes).select_related('item_catalogo'):
        itens_por_setor.setdefault(linha.setor_id, []).append(linha)
    for s in matrizes:
        s.itens_matriz = itens_por_setor.get(s.id, [])

    return render(request, 'funcionarios/epi/matriz_setores.html', {
        'matrizes': matrizes,
        'setores': setores,
        'setor': setor,
        'linhas_catalogo': linhas_catalogo,
    })


# ---------- Não conformidades ----------

def _data_ou_none(texto):
    try:
        return parse_date(texto) if texto else None
    except ValueError:
        return None


@login_required
@papel_required('seguranca_trabalho', 'rh')
def listar_ocorrencias_epi_view(request):
    busca = request.GET.get('busca', '').strip()
    data_inicio = request.GET.get('de', '')
    data_fim = request.GET.get('ate', '')

    ocorrencias = OcorrenciaEPI.objects.select_related('funcionario', 'item_catalogo', 'tecnico__usuario__funcionario')
    if busca:
        ocorrencias = ocorrencias.filter(funcionario__nome__icontains=busca)
    # Datas vêm de <input type="date">: formato inválido só é ignorado.
    if _data_ou_none(data_inicio):
        ocorrencias = ocorrencias.filter(data_ocorrencia__gte=_data_ou_none(data_inicio))
    if _data_ou_none(data_fim):
        ocorrencias = ocorrencias.filter(data_ocorrencia__lte=_data_ou_none(data_fim))
    # Termo de advertência assinado esperando validação vem primeiro: é a fila de trabalho.
    ocorrencias = ocorrencias.annotate(
        assinatura_a_validar=Case(When(status_assinatura='em_validacao', then=Value(0)), default=Value(1),
                                  output_field=IntegerField()),
    ).order_by('assinatura_a_validar', '-data_ocorrencia', '-criado_em')

    return render(request, 'funcionarios/epi/listar_ocorrencias.html', {
        'abrir_pendente': request.GET.get('pendentes') == '1',
        'ocorrencias': ocorrencias,
        'busca': busca,
        'de': data_inicio,
        'ate': data_fim,
    })


@login_required
@papel_required('seguranca_trabalho', 'rh')
def criar_ocorrencia_epi_view(request):
    if request.method == 'POST':
        form = OcorrenciaEPIForm(request.POST, request.FILES)
        if form.is_valid():
            ocorrencia = form.save(commit=False)
            ocorrencia.tecnico = request.user
            if ocorrencia.tipo == 'advertencia':
                ocorrencia.status_advertencia = 'pendente_gestor'
            ocorrencia.save()
            notificacoes.ocorrencia_epi_registrada(ocorrencia)
            if ocorrencia.tipo == 'advertencia':
                messages.success(request, 'Advertência registrada e enviada ao gestor para aprovação.')
            else:
                messages.success(request, 'Não conformidade registrada como aviso. Colaborador e gestor foram avisados.')
            return redirect('listar_ocorrencias_epi')
    else:
        form = OcorrenciaEPIForm(initial={
            'data_ocorrencia': timezone.now().date(), 'tipo': 'aviso', 'funcionario': _funcionario_pedido(request),
        })

    return render(request, 'funcionarios/epi/criar_ocorrencia.html', {
        'form': form,
        'funcionarios_busca': _funcionarios_busca(form.fields['funcionario'].queryset),
    })


@login_required
def ver_foto_ocorrencia_view(request, ocorrencia_id):
    """Foto da ocorrência: quem opera o módulo, o gestor da equipe e o próprio colaborador."""
    ocorrencia = get_object_or_404(OcorrenciaEPI, id=ocorrencia_id)
    try:
        usuario = request.user.usuario
    except Usuario.DoesNotExist:
        raise Http404
    pode_ver = (
        usuario.eh_seguranca_ou_rh()
        or usuario.funcionario_id == ocorrencia.funcionario_id
        or (usuario.papel == 'gestor' and usuario.equipe().filter(id=ocorrencia.funcionario_id).exists())
    )
    if not pode_ver:
        raise Http404
    if not ocorrencia.foto:
        raise Http404('Ocorrência sem foto.')
    return resposta_de_arquivo(ocorrencia.foto)


# ---------- Funcionário, na visão da Segurança do Trabalho ----------

@login_required
@papel_required('seguranca_trabalho', 'rh')
def funcionario_epi_view(request, funcionario_id):
    """
    O que a Segurança do Trabalho abre a partir da lista de funcionários:
    identificação e tudo de EPI (em uso, matriz do setor, fichas, ocorrências,
    pedidos de troca). Sem CPF, documentos, salário, ponto ou acesso -- isso
    fica no detalhe do RH/DP (`detalhe_funcionario`).
    """
    funcionario = get_object_or_404(Funcionario.objects.select_related('setor'), id=funcionario_id)
    hoje = timezone.localdate()

    itens = list(_epis_em_uso(funcionario))
    trocas_pendentes = set(
        SolicitacaoTrocaEPI.objects.filter(item__in=itens, status='pendente').values_list('item_id', flat=True)
    )
    for item in itens:
        item.troca_pendente = item.id in trocas_pendentes
        item.troca_vencida = bool(item.data_vencimento and item.data_vencimento <= hoje)

    # EPIs que a matriz do setor exige, marcando os que a pessoa já tem em uso.
    em_uso = {item.item_catalogo_id for item in itens}
    matriz = []
    if funcionario.setor_id:
        linhas = MatrizEpiSetor.objects.filter(
            setor_id=funcionario.setor_id, item_catalogo__ativo=True,
        ).select_related('item_catalogo').order_by('item_catalogo__nome')
        for linha in linhas:
            linha.em_uso = linha.item_catalogo_id in em_uso
            matriz.append(linha)

    return render(request, 'funcionarios/epi/funcionario_epi.html', {
        'funcionario': funcionario,
        'itens': itens,
        'matriz': matriz,
        'faltando_na_matriz': sum(1 for linha in matriz if not linha.em_uso),
        'fichas': funcionario.fichas_epi.annotate(total_itens=Count('itens')).order_by('-gerado_em'),
        'ocorrencias': funcionario.ocorrencias_epi.select_related('item_catalogo').order_by(
            '-data_ocorrencia', '-criado_em',
        ),
        'solicitacoes': funcionario.solicitacoes_troca_epi.select_related('item__item_catalogo').order_by(
            '-criada_em',
        )[:10],
        # Ficha e ocorrência só para quem não foi desligado (os formulários também barram).
        'pode_receber': funcionario.status != 'inativo',
    })


def _funcionario_pedido(request):
    """`?funcionario=<id>` vindo da ficha de EPI do funcionário: o formulário já abre com a pessoa escolhida."""
    valor = request.GET.get('funcionario', '')
    return int(valor) if valor.isdigit() else None


# ---------- Troca solicitada pelo colaborador ----------
# EPI que ficou inutilizado antes da data de troca: o colaborador pede em
# Meus EPIs (com foto, se quiser) e quem opera o módulo confirma ou recusa.

def _funcionario_logado(request):
    try:
        return request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return None


def _epis_em_uso(funcionario):
    """Itens entregues (ficha validada) e ainda com o colaborador."""
    return ItemEPI.objects.filter(
        ficha__funcionario=funcionario, ficha__status='ativo', devolvido=False,
    ).select_related('item_catalogo', 'ficha').order_by('item_catalogo__nome')


@login_required
def meus_epis_view(request):
    funcionario = _funcionario_logado(request)
    if funcionario is None:
        return render(request, 'funcionarios/sem_perfil.html')

    itens = list(_epis_em_uso(funcionario))
    pendentes = set(
        SolicitacaoTrocaEPI.objects.filter(item__in=itens, status='pendente').values_list('item_id', flat=True)
    )
    for item in itens:
        item.troca_pendente = item.id in pendentes

    return render(request, 'funcionarios/epi/meus_epis.html', {
        'itens': itens,
        'solicitacoes': funcionario.solicitacoes_troca_epi.select_related('item__item_catalogo')[:20],
        'ocorrencias': OcorrenciaEPI.visiveis_ao_colaborador(funcionario).select_related('item_catalogo').order_by(
            '-data_ocorrencia', '-criado_em',
        )[:20],
        'form': SolicitacaoTrocaEPIForm(),
    })


@login_required
@require_POST
def solicitar_troca_epi_view(request, item_id):
    funcionario = _funcionario_logado(request)
    if funcionario is None:
        return redirect('dashboard')
    # Só item do próprio colaborador e ainda em uso.
    item = get_object_or_404(_epis_em_uso(funcionario), id=item_id)

    if item.solicitacoes_troca.filter(status='pendente').exists():
        messages.warning(request, f'Já existe um pedido de troca em análise para {item.item_catalogo.nome}.')
        return redirect('meus_epis')

    form = SolicitacaoTrocaEPIForm(request.POST, request.FILES)
    if not form.is_valid():
        erros = [erro for lista in form.errors.values() for erro in lista]
        messages.error(request, f'Pedido não enviado. {erros[0]}')
        return redirect('meus_epis')

    solicitacao = form.save(commit=False)
    solicitacao.item = item
    solicitacao.funcionario = funcionario
    solicitacao.save()
    notificacoes.troca_epi_solicitada(solicitacao)
    messages.success(request, f'Pedido de troca de {item.item_catalogo.nome} enviado. Aguarde a análise.')
    return redirect('meus_epis')


@login_required
@papel_required('seguranca_trabalho', 'rh')
def listar_trocas_epi_view(request):
    solicitacoes = SolicitacaoTrocaEPI.objects.select_related(
        'funcionario__setor', 'item__item_catalogo', 'item__ficha', 'respondida_por__usuario__funcionario', 'nova_ficha',
    )
    pendentes = list(solicitacoes.filter(status='pendente').order_by('criada_em'))
    for solicitacao in pendentes:
        solicitacao.form_confirmar = ConfirmarTrocaEPIForm(
            initial={'quantidade': solicitacao.item.quantidade}, prefix=f'troca{solicitacao.id}',
        )

    return render(request, 'funcionarios/epi/trocas_solicitadas.html', {
        'pendentes': pendentes,
        'respondidas': solicitacoes.exclude(status='pendente').order_by('-respondida_em')[:30],
        'total_pendencias_epi': _total_pendencias_epi(),
    })


def _solicitacao_pendente(solicitacao_id):
    return get_object_or_404(
        SolicitacaoTrocaEPI.objects.select_related('funcionario', 'item__item_catalogo'),
        id=solicitacao_id, status='pendente',
    )


@login_required
@papel_required('seguranca_trabalho', 'rh')
@require_POST
def confirmar_troca_epi_view(request, solicitacao_id):
    solicitacao = _solicitacao_pendente(solicitacao_id)
    form = ConfirmarTrocaEPIForm(request.POST, prefix=f'troca{solicitacao.id}')
    if not form.is_valid():
        messages.error(request, 'Confira a quantidade e a data de troca do EPI substituto.')
        return redirect('listar_trocas_epi')

    with transaction.atomic():
        # O EPI inutilizado sai de uso; o substituto vai numa ficha nova, que o
        # colaborador assina como qualquer entrega.
        ItemEPI.objects.filter(id=solicitacao.item_id).update(devolvido=True)
        ficha = FichaEPI.objects.create(funcionario=solicitacao.funcionario, gerado_por=request.user)
        ItemEPI.objects.create(
            ficha=ficha, item_catalogo=solicitacao.item.item_catalogo,
            quantidade=form.cleaned_data['quantidade'], data_vencimento=form.cleaned_data['data_vencimento'],
        )
        solicitacao.status = 'aprovada'
        solicitacao.respondida_por = request.user
        solicitacao.respondida_em = timezone.now()
        solicitacao.nova_ficha = ficha
        solicitacao.save()

    pdf_ok = _gerar_pdf_ficha(ficha)
    notificacoes.troca_epi_respondida(solicitacao)
    if pdf_ok:
        messages.success(request, f'Troca confirmada. A ficha do novo EPI foi enviada a {solicitacao.funcionario.nome} para assinatura.')
    else:
        messages.warning(request, 'Troca confirmada, mas o PDF da nova ficha não pôde ser gerado. Use "Gerar PDF" em Fichas.')
    return redirect('listar_trocas_epi')


@login_required
@papel_required('seguranca_trabalho', 'rh')
@require_POST
def recusar_troca_epi_view(request, solicitacao_id):
    solicitacao = _solicitacao_pendente(solicitacao_id)
    motivo = request.POST.get('motivo_recusa', '').strip()
    if not motivo:
        messages.error(request, 'Informe o motivo da recusa: o colaborador vai ver.')
        return redirect('listar_trocas_epi')

    solicitacao.status = 'recusada'
    solicitacao.motivo_recusa = motivo
    solicitacao.respondida_por = request.user
    solicitacao.respondida_em = timezone.now()
    solicitacao.save()
    notificacoes.troca_epi_respondida(solicitacao)
    messages.success(request, f'Pedido de troca de {solicitacao.funcionario.nome} recusado.')
    return redirect('listar_trocas_epi')


@login_required
def ver_foto_troca_epi_view(request, solicitacao_id):
    """A foto só abre para quem pediu e para quem opera o módulo de EPI."""
    solicitacao = get_object_or_404(SolicitacaoTrocaEPI, id=solicitacao_id)
    try:
        usuario = request.user.usuario
    except Usuario.DoesNotExist:
        raise Http404
    if not (usuario.funcionario_id == solicitacao.funcionario_id or usuario.eh_seguranca_ou_rh()):
        raise Http404
    if not solicitacao.foto:
        raise Http404('Pedido sem foto.')
    return resposta_de_arquivo(solicitacao.foto)


# ---------- Ocorrências da equipe (gestor) ----------
# O gestor é avisado de toda ocorrência da equipe e aprova ou recusa as
# advertências. Aprovada, o colaborador perde assiduidade e vale-alimentação
# no mês da ocorrência (folha.services.calcular_ponto).

def _ocorrencias_da_equipe(usuario):
    return OcorrenciaEPI.objects.filter(funcionario__in=usuario.equipe()).select_related(
        'funcionario__setor', 'item_catalogo', 'tecnico__usuario__funcionario', 'decidido_por__usuario__funcionario',
    )


@login_required
@papel_required('gestor')
def ocorrencias_equipe_view(request):
    ocorrencias = _ocorrencias_da_equipe(request.user.usuario)
    return render(request, 'funcionarios/epi/ocorrencias_equipe.html', {
        'pendentes': ocorrencias.filter(tipo='advertencia', status_advertencia='pendente_gestor').order_by('data_ocorrencia'),
        'historico': ocorrencias.exclude(status_advertencia='pendente_gestor').order_by('-data_ocorrencia', '-criado_em')[:50],
    })


def _aplicar_advertencia_na_folha(ocorrencia):
    """
    Recalcula a linha do colaborador no fechamento do mês da ocorrência, se
    ele estiver aberto. Devolve o aviso para a tela quando não deu para aplicar.
    """
    from folha.models import Fechamento
    from folha.services import recalcular_linha

    fechamento = Fechamento.objects.filter(competencia=ocorrencia.mes_da_perda).first()
    mes = ocorrencia.mes_da_perda.strftime('%m/%Y')
    if fechamento is None:
        return ''  # o fechamento ainda vai ser aberto e já calcula com a advertência
    if fechamento.status != 'aberto':
        return f' O fechamento de {mes} já está fechado: a perda só entra se ele for reaberto e recalculado.'
    linha = fechamento.linhas.filter(funcionario=ocorrencia.funcionario).select_related('funcionario', 'fechamento').first()
    if linha is not None:
        recalcular_linha(linha)
    return ''


def _advertencia_pendente_da_equipe(request, ocorrencia_id):
    return get_object_or_404(
        _ocorrencias_da_equipe(request.user.usuario), id=ocorrencia_id,
        tipo='advertencia', status_advertencia='pendente_gestor',
    )


@login_required
@papel_required('gestor')
@require_POST
def aprovar_advertencia_view(request, ocorrencia_id):
    ocorrencia = _advertencia_pendente_da_equipe(request, ocorrencia_id)
    ocorrencia.status_advertencia = 'aprovada'
    ocorrencia.decidido_por = request.user
    ocorrencia.decidido_em = timezone.now()
    ocorrencia.motivo_recusa = ''
    ocorrencia.save()
    aviso_folha = _aplicar_advertencia_na_folha(ocorrencia)
    termo_ok = _gerar_pdf_advertencia(ocorrencia)
    notificacoes.advertencia_epi_decidida(ocorrencia)
    mes = ocorrencia.mes_da_perda.strftime('%m/%Y')
    aviso_termo = (' O termo foi enviado ao colaborador para assinar.' if termo_ok
                   else ' O termo não pôde ser gerado: a Segurança do Trabalho gera de novo em Não Conformidades.')
    messages.success(
        request,
        f'Advertência de {ocorrencia.funcionario.nome} aprovada: perde assiduidade e vale-alimentação de {mes}.'
        f'{aviso_folha}{aviso_termo}',
    )
    return redirect('ocorrencias_equipe')


@login_required
@papel_required('gestor')
@require_POST
def recusar_advertencia_view(request, ocorrencia_id):
    ocorrencia = _advertencia_pendente_da_equipe(request, ocorrencia_id)
    motivo = request.POST.get('motivo_recusa', '').strip()
    if not motivo:
        messages.error(request, 'Informe o motivo da recusa: a Segurança do Trabalho vai ver.')
        return redirect('ocorrencias_equipe')
    ocorrencia.status_advertencia = 'recusada'
    ocorrencia.decidido_por = request.user
    ocorrencia.decidido_em = timezone.now()
    ocorrencia.motivo_recusa = motivo
    ocorrencia.save()
    notificacoes.advertencia_epi_decidida(ocorrencia)
    messages.success(request, f'Advertência de {ocorrencia.funcionario.nome} recusada. Fica registrada só como ocorrência.')
    return redirect('ocorrencias_equipe')


# ---------- Termo de advertência (assinatura do colaborador) ----------
# Mesmo ciclo da ficha de EPI: gerado na aprovação do gestor, o colaborador
# baixa, assina e devolve em Meus Documentos, e quem opera o módulo valida.

def _gerar_pdf_advertencia(ocorrencia):
    """Gera o termo e o deixa aguardando a assinatura do colaborador. Devolve False se o PDF falhar."""
    gestor = ocorrencia.decidido_por
    nome_gestor = ''
    if gestor is not None:
        nome_gestor = getattr(getattr(gestor, 'usuario', None), 'funcionario', None)
        nome_gestor = nome_gestor.nome if nome_gestor else gestor.get_full_name() or gestor.username
    html = render_to_string('funcionarios/epi/advertencia_epi.html', {
        'ocorrencia': ocorrencia,
        'funcionario': ocorrencia.funcionario,
        'cpf_formatado': formatar_cpf(ocorrencia.funcionario.cpf),
        'mes_da_perda': ocorrencia.mes_da_perda.strftime('%m/%Y'),
        'gestor': nome_gestor,
        'data_extenso': data_por_extenso(timezone.localdate()),
        'caminho_logo': os.path.join(settings.BASE_DIR, 'static', 'img', 'logo-sotrigo.png'),
    })
    buffer = io.BytesIO()
    if pisa.CreatePDF(html, dest=buffer).err:
        return False
    nome_arquivo = f'advertencia_epi_{ocorrencia.id}_{ocorrencia.funcionario.cpf}.pdf'
    ocorrencia.arquivo_advertencia.save(nome_arquivo, ContentFile(buffer.getvalue()), save=False)
    ocorrencia.status_assinatura = 'aguardando_assinatura'
    ocorrencia.save()
    return True


def _pode_ver_advertencia(usuario, ocorrencia):
    return (
        usuario.eh_seguranca_ou_rh()
        or usuario.funcionario_id == ocorrencia.funcionario_id
        or (usuario.papel == 'gestor' and usuario.equipe().filter(id=ocorrencia.funcionario_id).exists())
    )


@login_required
@xframe_options_sameorigin  # a lista de ocorrências exibe o termo num iframe, dentro do próprio sistema
def ver_advertencia_epi_view(request, ocorrencia_id, versao):
    if versao not in ('gerado', 'assinado'):
        raise Http404
    ocorrencia = get_object_or_404(OcorrenciaEPI, id=ocorrencia_id, tipo='advertencia', status_advertencia='aprovada')
    try:
        usuario = request.user.usuario
    except Usuario.DoesNotExist:
        raise Http404
    if not _pode_ver_advertencia(usuario, ocorrencia):
        raise Http404
    arquivo = ocorrencia.arquivo_advertencia_assinado if versao == 'assinado' else ocorrencia.arquivo_advertencia
    if not arquivo:
        raise Http404('Termo não disponível.')
    return resposta_de_arquivo(arquivo)


# Sem @papel_required de propósito: quem envia é o próprio colaborador, e a
# posse é garantida pelo filtro por funcionário no get_object_or_404.
@login_required
@require_POST
def reenviar_advertencia_assinada_view(request, ocorrencia_id):
    funcionario = _funcionario_logado(request)
    if funcionario is None:
        return redirect('dashboard')
    ocorrencia = get_object_or_404(OcorrenciaEPI, id=ocorrencia_id, funcionario=funcionario)
    destino = 'minhas_assinaturas' if request.POST.get('origem') == 'assinatura_gov' else 'meus_documentos'

    if ocorrencia.status_assinatura != 'aguardando_assinatura' or not ocorrencia.arquivo_advertencia:
        messages.error(request, 'Este termo de advertência não está aguardando assinatura.')
        return redirect(destino)
    if not request.FILES.get('arquivo_assinado'):
        messages.error(request, 'Escolha o arquivo do termo assinado.')
        return redirect(destino)
    try:
        arquivo = validar_upload(request.FILES['arquivo_assinado'])
    except ValidationError as erro:
        messages.error(request, f'Termo não enviado. {erro.messages[0]}')
        return redirect(destino)

    ocorrencia.arquivo_advertencia_assinado = arquivo
    ocorrencia.status_assinatura = 'em_validacao'
    ocorrencia.motivo_rejeicao_assinatura = ''
    ocorrencia.save()
    notificacoes.advertencia_assinada_enviada(ocorrencia)
    messages.success(request, 'Termo de advertência assinado enviado. Aguarde a validação da Segurança do Trabalho.')
    return redirect(destino)


def _voltar_para_ocorrencias(request):
    destino = request.POST.get('next', '')
    if url_has_allowed_host_and_scheme(destino, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(destino)
    return redirect('listar_ocorrencias_epi')


@login_required
@papel_required('seguranca_trabalho', 'rh')
@require_POST
def aprovar_assinatura_advertencia_view(request, ocorrencia_id):
    ocorrencia = get_object_or_404(OcorrenciaEPI, id=ocorrencia_id)
    if ocorrencia.status_assinatura != 'em_validacao':
        messages.error(request, 'Este termo não está aguardando validação.')
        return _voltar_para_ocorrencias(request)
    ocorrencia.status_assinatura = 'assinada'
    ocorrencia.motivo_rejeicao_assinatura = ''
    ocorrencia.assinatura_validada_por = request.user
    ocorrencia.assinatura_validada_em = timezone.now()
    ocorrencia.save()
    messages.success(request, f'Termo de advertência de {ocorrencia.funcionario.nome} validado.')
    return _voltar_para_ocorrencias(request)


@login_required
@papel_required('seguranca_trabalho', 'rh')
@require_POST
def rejeitar_assinatura_advertencia_view(request, ocorrencia_id):
    ocorrencia = get_object_or_404(OcorrenciaEPI, id=ocorrencia_id)
    if ocorrencia.status_assinatura != 'em_validacao':
        messages.error(request, 'Este termo não está aguardando validação.')
        return _voltar_para_ocorrencias(request)
    form = RejeitarDocumentoForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Informe o motivo da rejeição.')
        return _voltar_para_ocorrencias(request)
    # Volta para o colaborador assinar de novo; o termo gerado continua o mesmo.
    ocorrencia.status_assinatura = 'aguardando_assinatura'
    ocorrencia.motivo_rejeicao_assinatura = form.cleaned_data['motivo']
    ocorrencia.assinatura_validada_por = request.user
    ocorrencia.assinatura_validada_em = timezone.now()
    ocorrencia.save()
    notificacoes.assinatura_advertencia_rejeitada(ocorrencia)
    messages.success(request, 'Assinatura rejeitada. O colaborador poderá reenviar o termo.')
    return _voltar_para_ocorrencias(request)


@login_required
@papel_required('seguranca_trabalho', 'rh')
@require_POST
def gerar_pdf_advertencia_view(request, ocorrencia_id):
    """Para quando o PDF falhou na aprovação do gestor."""
    ocorrencia = get_object_or_404(OcorrenciaEPI, id=ocorrencia_id, tipo='advertencia', status_advertencia='aprovada')
    if ocorrencia.arquivo_advertencia:
        messages.info(request, 'O termo desta advertência já foi gerado.')
    elif _gerar_pdf_advertencia(ocorrencia):
        messages.success(request, 'Termo de advertência gerado e enviado ao colaborador para assinar.')
    else:
        messages.error(request, 'Não foi possível gerar o termo. Tente novamente.')
    return redirect('listar_ocorrencias_epi')
