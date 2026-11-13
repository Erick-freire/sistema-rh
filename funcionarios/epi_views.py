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
from django.db.models import Case, Count, IntegerField, Prefetch, Value, When
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
from .forms import FichaEPIForm, OcorrenciaEPIForm, RejeitarDocumentoForm, item_epi_formset
from .models import FichaEPI, Funcionario, ItemCatalogoEPI, ItemEPI, MatrizEpiSetor, OcorrenciaEPI, Setor, Usuario
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
    fichas = FichaEPI.objects.select_related('funcionario').annotate(
        aguardando_validacao=Case(When(status='em_validacao', then=Value(0)), default=Value(1), output_field=IntegerField()),
        total_itens=Count('itens'),
    ).order_by('aguardando_validacao', '-gerado_em')

    return render(request, 'funcionarios/epi/listar_fichas.html', {
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
        form = FichaEPIForm()
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
        'data_extenso': data_por_extenso(ficha.data_entrega),
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

    return render(request, 'funcionarios/epi/epis_vencendo.html', {
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


# ---------- Matriz de EPI por setor ----------

@login_required
@papel_required('seguranca_trabalho', 'rh')
def gerenciar_matriz_epi_view(request):
    setores = Setor.objects.annotate(total=Count('matriz_epi')).order_by('nome')

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
        return redirect(f'{request.path}?setor={setor.id}')

    linhas_catalogo = []
    if setor:
        atuais = dict(setor.matriz_epi.values_list('item_catalogo_id', 'quantidade_padrao'))
        linhas_catalogo = [
            {'item': item, 'marcado': item.id in atuais, 'quantidade': atuais.get(item.id, 1)}
            for item in catalogo
        ]

    return render(request, 'funcionarios/epi/matriz_setores.html', {
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
    ocorrencias = ocorrencias.order_by('-data_ocorrencia', '-criado_em')

    return render(request, 'funcionarios/epi/listar_ocorrencias.html', {
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
            ocorrencia.save()
            messages.success(request, 'Não conformidade registrada.')
            return redirect('listar_ocorrencias_epi')
    else:
        form = OcorrenciaEPIForm(initial={'data_ocorrencia': timezone.now().date()})

    return render(request, 'funcionarios/epi/criar_ocorrencia.html', {
        'form': form,
        'funcionarios_busca': _funcionarios_busca(form.fields['funcionario'].queryset),
    })


@login_required
@papel_required('seguranca_trabalho', 'rh')
def ver_foto_ocorrencia_view(request, ocorrencia_id):
    ocorrencia = get_object_or_404(OcorrenciaEPI, id=ocorrencia_id)
    if not ocorrencia.foto:
        raise Http404('Ocorrência sem foto.')
    return resposta_de_arquivo(ocorrencia.foto)
