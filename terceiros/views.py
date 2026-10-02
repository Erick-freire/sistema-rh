import io
import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from xhtml2pdf import pisa

from funcionarios.decorators import papel_required
from funcionarios.perfis import responsavel_terceiro_de, usuario_de
from funcionarios.utils import data_por_extenso

from .decorators import responsavel_required
from .forms import (
    CadastroEmpresaForm,
    ClausulaContratualForm,
    ColaboradorTerceiroForm,
    ContratoTerceiroForm,
    DadosEmpresaForm,
    DadosResponsavelForm,
    EnviarDocumentoColaboradorForm,
    EnviarDocumentoEmpresaForm,
    RejeitarDocumentoTerceiroForm,
)
from .models import (
    ClausulaContratual,
    ColaboradorTerceiro,
    ContratoTerceiro,
    DocumentoColaborador,
    DocumentoEmpresaTerceira,
    EmpresaTerceira,
    ResponsavelTerceiro,
)

STATUS_EM_ANDAMENTO = [
    'pendente_cadastro', 'aguardando_contrato', 'aguardando_assinatura', 'em_validacao',
]


class FalhaNaGeracaoDoPdf(Exception):
    """Aborta a transação da geração de contrato quando o xhtml2pdf falha."""


# ---------- Empresas terceiras (RH) ----------

@login_required
@papel_required('rh')
def listar_empresas_view(request):
    busca = request.GET.get('busca', '').strip()
    status_filtro = request.GET.get('status', 'em_andamento')

    empresas = EmpresaTerceira.objects.select_related('responsavel')

    if busca:
        empresas = empresas.filter(
            Q(razao_social__icontains=busca)
            | Q(nome_fantasia__icontains=busca)
            | Q(cnpj__icontains=busca)
        )

    if status_filtro == 'ativo':
        empresas = empresas.filter(status='ativo')
    elif status_filtro == 'inativo':
        empresas = empresas.filter(status='inativo')
    else:
        status_filtro = 'em_andamento'
        empresas = empresas.filter(status__in=STATUS_EM_ANDAMENTO)

    # `order_by` explícito: o `Meta.ordering` do model não vale em consulta com
    # annotate/GROUP BY, e sem ordem a paginação fica instável entre páginas.
    empresas = empresas.annotate(total_colaboradores=Count('colaboradores')).order_by('razao_social')

    contagens = EmpresaTerceira.objects.aggregate(
        em_andamento=Count('pk', filter=Q(status__in=STATUS_EM_ANDAMENTO)),
        ativos=Count('pk', filter=Q(status='ativo')),
        inativos=Count('pk', filter=Q(status='inativo')),
    )

    paginador = Paginator(empresas, 20)
    pagina = paginador.get_page(request.GET.get('page'))

    return render(request, 'terceiros/rh/listar_empresas.html', {
        'pagina': pagina,
        'busca': busca,
        'status_filtro': status_filtro,
        'contagem_em_andamento': contagens['em_andamento'],
        'contagem_ativos': contagens['ativos'],
        'contagem_inativos': contagens['inativos'],
    })


@login_required
@papel_required('rh')
def cadastrar_empresa_view(request):
    if request.method == 'POST':
        form = CadastroEmpresaForm(request.POST)
        if form.is_valid():
            senha_gerada = secrets.token_urlsafe(9)
            cpf = form.cleaned_data['responsavel_cpf']

            # Empresa, usuário e responsável nascem juntos ou nenhum nasce: uma
            # empresa sem responsável ficaria travada em `pendente_cadastro` sem
            # ninguém capaz de completar o cadastro.
            with transaction.atomic():
                empresa = form.save(commit=False)
                empresa.status = 'pendente_cadastro'
                empresa.save()

                user = User.objects.create_user(username=cpf, password=senha_gerada)

                ResponsavelTerceiro.objects.create(
                    empresa=empresa,
                    user=user,
                    nome=form.cleaned_data['responsavel_nome'],
                    cpf=cpf,
                    senha_provisoria=True,
                )

            return render(request, 'terceiros/rh/credenciais_geradas.html', {
                'empresa': empresa,
                'username': user.username,
                'senha_gerada': senha_gerada,
            })
    else:
        form = CadastroEmpresaForm()

    return render(request, 'terceiros/rh/cadastrar_empresa.html', {'form': form})


def _pode_analisar_documentos(empresa):
    """
    Janela em que o RH aprova ou reprova documento da equipe.

    Abre assim que a prestadora começa a enviar (`aguardando_assinatura`), e não
    só depois do "Enviar para validação": o RH pode ir revisando conforme chega,
    e o responsável recebe a reprovação sem esperar fechar o lote. Fecha quando a
    empresa é validada -- daí a documentação já está aprovada.
    """
    return empresa.status in ('aguardando_assinatura', 'em_validacao')


def _pode_analisar_mensais(empresa):
    """
    Janela dos documentos mensais (FGTS, DCTFWeb, holerite).

    Diferente da janela do onboarding, esta continua aberta com a empresa já
    ativa -- é justamente aí que os mensais começam a chegar, mês a mês. Fecha só
    quando a prestadora é inativada.
    """
    return empresa.status in ('aguardando_assinatura', 'em_validacao', 'ativo')


def _pode_analisar_documento_de(documento, empresa):
    """O holerite segue a janela dos mensais; o resto, a do onboarding."""
    if documento.tipo in DocumentoColaborador.TIPOS_MENSAIS:
        return _pode_analisar_mensais(empresa)
    return _pode_analisar_documentos(empresa)


@login_required
@papel_required('rh')
def detalhe_empresa_view(request, empresa_id):
    empresa = get_object_or_404(
        EmpresaTerceira.objects.select_related('responsavel'), id=empresa_id
    )

    colaboradores = (
        empresa.colaboradores
        .prefetch_related('documentos')
        .all()
    )

    return render(request, 'terceiros/rh/detalhe_empresa.html', {
        'empresa': empresa,
        'responsavel': getattr(empresa, 'responsavel', None),
        'colaboradores': colaboradores,
        'contrato': empresa.contrato_atual(),
        'pode_analisar_documentos': _pode_analisar_documentos(empresa),
        'pode_analisar_mensais': _pode_analisar_mensais(empresa),
        'pode_validar': empresa.documentos_todos_aprovados(),
        'competencias_mensais': empresa.competencias_mensais(),
    })


@login_required
@papel_required('rh')
def resetar_senha_responsavel_view(request, empresa_id):
    empresa = get_object_or_404(EmpresaTerceira, id=empresa_id)
    responsavel = getattr(empresa, 'responsavel', None)

    if responsavel is None:
        messages.error(request, 'Esta empresa não tem responsável cadastrado.')
        return redirect('terceiros:detalhe_empresa', empresa_id=empresa.id)

    senha_gerada = secrets.token_urlsafe(9)
    responsavel.user.set_password(senha_gerada)
    responsavel.user.save()
    responsavel.senha_provisoria = True
    responsavel.save()

    return render(request, 'terceiros/rh/credenciais_geradas.html', {
        'empresa': empresa,
        'username': responsavel.user.username,
        'senha_gerada': senha_gerada,
        'reset_senha': True,
    })


@login_required
@papel_required('rh')
def alternar_status_empresa_view(request, empresa_id):
    empresa = get_object_or_404(EmpresaTerceira, id=empresa_id)

    if empresa.status == 'inativo':
        empresa.status = 'ativo'
        messages.success(request, f'{empresa.nome_curto} reativada.')
    elif empresa.status == 'ativo':
        empresa.status = 'inativo'
        messages.success(request, f'{empresa.nome_curto} inativada.')
    else:
        messages.error(request, 'Só é possível ativar ou inativar uma empresa já validada.')

    empresa.save()
    return redirect('terceiros:detalhe_empresa', empresa_id=empresa.id)


# ---------- Contrato (RH) ----------

def _valores_marcadores(empresa, responsavel, prazo_meses):
    """Preenche os marcadores `{{...}}` aceitos no texto das cláusulas."""
    return {
        '{{razao_social}}': empresa.razao_social,
        '{{nome_fantasia}}': empresa.nome_curto,
        '{{cnpj}}': empresa.cnpj_formatado,
        '{{endereco_contratada}}': empresa.endereco_completo,
        '{{nome_prestador}}': responsavel.nome if responsavel else '',
        '{{cpf_prestador}}': responsavel.cpf_formatado if responsavel else '',
        '{{prazo_meses}}': prazo_meses,
        '{{cidade}}': empresa.cidade,
    }


@login_required
@papel_required('rh')
def gerar_contrato_view(request, empresa_id):
    empresa = get_object_or_404(
        EmpresaTerceira.objects.select_related('responsavel'), id=empresa_id
    )
    responsavel = getattr(empresa, 'responsavel', None)

    if responsavel is None or not responsavel.dados_completos():
        messages.error(
            request,
            'O responsável ainda não completou os dados dele. O contrato precisa desses dados '
            'para ser gerado.',
        )
        return redirect('terceiros:detalhe_empresa', empresa_id=empresa.id)

    if empresa.status not in ('aguardando_contrato', 'aguardando_assinatura'):
        messages.error(request, 'Esta empresa não está na etapa de geração de contrato.')
        return redirect('terceiros:detalhe_empresa', empresa_id=empresa.id)

    if request.method == 'POST':
        form = ContratoTerceiroForm(request.POST)
        if form.is_valid():
            # Contrato, cláusula salva na biblioteca, PDF e mudança de status são
            # uma coisa só: um contrato gravado sem PDF deixaria a empresa em
            # `aguardando_assinatura` sem nada para o responsável baixar.
            try:
                with transaction.atomic():
                    contrato = form.save(commit=False)
                    contrato.empresa = empresa
                    contrato.gerado_por = request.user
                    contrato.save()
                    form.save_m2m()

                    # "Guardar na biblioteca" é ação explícita do RH, não efeito
                    # colateral de ter digitado texto avulso.
                    if form.cleaned_data.get('salvar_avulsas_na_biblioteca'):
                        ultima_ordem = (
                            ClausulaContratual.objects
                            .order_by('-ordem_sugerida')
                            .values_list('ordem_sugerida', flat=True)
                            .first()
                        ) or 0
                        ClausulaContratual.objects.create(
                            titulo=form.cleaned_data['titulo_avulsas'].strip(),
                            texto=contrato.clausulas_avulsas,
                            ordem_sugerida=ultima_ordem + 10,
                        )

                    pdf_bytes = _montar_pdf_contrato(contrato, empresa, responsavel)
                    if pdf_bytes is None:
                        raise FalhaNaGeracaoDoPdf

                    contrato.arquivo_gerado.save(
                        f'contrato_{empresa.cnpj}_{contrato.pk}.pdf',
                        ContentFile(pdf_bytes),
                        save=True,
                    )

                    empresa.status = 'aguardando_assinatura'
                    empresa.save()
            except FalhaNaGeracaoDoPdf:
                messages.error(request, 'Não foi possível gerar o PDF do contrato. Tente novamente.')
                return redirect('terceiros:gerar_contrato', empresa_id=empresa.id)

            messages.success(
                request,
                'Contrato gerado. O responsável já pode baixá-lo para colher as assinaturas.',
            )
            return redirect('terceiros:detalhe_empresa', empresa_id=empresa.id)
    else:
        form = ContratoTerceiroForm()

    return render(request, 'terceiros/rh/gerar_contrato.html', {
        'empresa': empresa,
        'responsavel': responsavel,
        'form': form,
        'marcadores': ClausulaContratual.MARCADORES.items(),
    })


def montar_clausulas(contrato, empresa, responsavel):
    """
    Monta a lista ordenada de cláusulas do contrato, na ordem em que saem no PDF:
    biblioteca marcada, os quatro campos estruturados e, por último, as avulsas.

    Cada item é `{'titulo', 'paragrafos'}`. O primeiro parágrafo é o texto que
    recebe o número da cláusula no template; os seguintes saem como parágrafos
    de continuação (equivalentes aos "§1º" do modelo). A numeração é sequencial
    e calculada na renderização -- por isso os textos da biblioteca não trazem
    número escrito, senão desmarcar uma cláusula furaria a sequência.
    """
    valores = _valores_marcadores(empresa, responsavel, contrato.prazo_meses)

    clausulas = [
        {'titulo': clausula.titulo, 'paragrafos': clausula.paragrafos_renderizados(valores)}
        for clausula in contrato.clausulas_biblioteca.all()
    ]

    def paragrafos(texto):
        return [linha.strip() for linha in (texto or '').splitlines() if linha.strip()]

    clausulas.append({'titulo': 'Do Objeto', 'paragrafos': paragrafos(contrato.objeto_servico)})
    clausulas.append({
        'titulo': 'Da Equipe Disponibilizada',
        'paragrafos': paragrafos(contrato.composicao_equipe),
    })
    clausulas.append({
        'titulo': 'Da Remuneração e Forma de Pagamento',
        'paragrafos': paragrafos(contrato.condicoes_remuneracao),
    })
    clausulas.append({
        'titulo': 'Do Prazo',
        'paragrafos': [
            f'O presente contrato vigorará pelo prazo de {contrato.prazo_meses} '
            f'{"mês" if contrato.prazo_meses == 1 else "meses"}, contado da data de sua assinatura, '
            'renovando-se automaticamente por igual período caso nenhuma das partes manifeste '
            'interesse contrário, por escrito, com antecedência mínima de 30 (trinta) dias do termo final.',
        ],
    })

    # Cada parágrafo avulso é uma cláusula própria ("uma por parágrafo"); só a
    # primeira leva o título da seção.
    for indice, paragrafo in enumerate(contrato.paragrafos_avulsos()):
        clausulas.append({
            'titulo': 'Das Disposições Específicas' if indice == 0 else None,
            'paragrafos': [paragrafo],
        })

    return [clausula for clausula in clausulas if clausula['paragrafos']]


def _montar_pdf_contrato(contrato, empresa, responsavel):
    """
    Renderiza o contrato (com o Termo de Confidencialidade anexado pelo próprio
    template) e devolve os bytes do PDF, ou None se o xhtml2pdf falhar.
    """
    html = render_to_string('terceiros/contrato_terceiro.html', {
        'empresa': empresa,
        'responsavel': responsavel,
        'contrato': contrato,
        'clausulas': montar_clausulas(contrato, empresa, responsavel),
        'data_extenso': data_por_extenso(timezone.now().date()),
    })

    buffer = io.BytesIO()
    resultado = pisa.CreatePDF(html, dest=buffer)
    if resultado.err:
        return None
    return buffer.getvalue()


# ---------- Biblioteca de cláusulas (RH) ----------

def _prefixo_edicao(clausula):
    """
    Cada cláusula da lista carrega seu próprio formulário de edição; o prefixo
    evita que os `id`/`name` dos campos colidam entre um card e outro (e com o
    formulário de nova cláusula) na mesma página.
    """
    return f'clausula{clausula.pk}'


def _render_biblioteca_clausulas(request, form, form_edicao=None, clausula_em_edicao=None):
    """
    Quando uma edição volta com erro, o formulário preenchido entra no lugar do
    formulário em branco daquela cláusula, para o RH não perder o que digitou.
    """
    clausulas = [
        (
            clausula,
            form_edicao
            if clausula_em_edicao is not None and clausula.pk == clausula_em_edicao.pk
            else ClausulaContratualForm(instance=clausula, prefix=_prefixo_edicao(clausula)),
        )
        for clausula in ClausulaContratual.objects.all()
    ]

    return render(request, 'terceiros/rh/listar_clausulas.html', {
        'clausulas': clausulas,
        'form': form,
        'clausula_em_edicao_id': clausula_em_edicao.pk if clausula_em_edicao is not None else None,
    })


@login_required
@papel_required('rh')
def listar_clausulas_view(request):
    if request.method == 'POST':
        form = ClausulaContratualForm(request.POST)
        if form.is_valid():
            clausula = form.save()
            messages.success(request, f'Cláusula "{clausula.titulo}" adicionada à biblioteca.')
            return redirect('terceiros:listar_clausulas')
        messages.error(request, 'Confira os campos da cláusula.')
    else:
        form = ClausulaContratualForm()

    return _render_biblioteca_clausulas(request, form)


@login_required
@papel_required('rh')
def editar_clausula_view(request, clausula_id):
    """
    Edita o texto da biblioteca. Os contratos já gerados não mudam: o PDF fica
    guardado em `ContratoTerceiro.arquivo_gerado`, não é remontado na hora.
    """
    clausula = get_object_or_404(ClausulaContratual, id=clausula_id)
    if request.method != 'POST':
        return redirect('terceiros:listar_clausulas')

    form = ClausulaContratualForm(
        request.POST, instance=clausula, prefix=_prefixo_edicao(clausula)
    )
    if form.is_valid():
        clausula = form.save()
        messages.success(request, f'Cláusula "{clausula.titulo}" atualizada.')
        return redirect('terceiros:listar_clausulas')

    messages.error(request, 'Confira os campos da cláusula.')
    return _render_biblioteca_clausulas(request, ClausulaContratualForm(), form, clausula)


@login_required
@papel_required('rh')
def alternar_clausula_view(request, clausula_id):
    clausula = get_object_or_404(ClausulaContratual, id=clausula_id)
    clausula.ativa = not clausula.ativa
    clausula.save()

    if clausula.ativa:
        messages.success(request, f'"{clausula.titulo}" volta a ser sugerida nos novos contratos.')
    else:
        messages.success(request, f'"{clausula.titulo}" não será mais sugerida nos novos contratos.')

    return redirect('terceiros:listar_clausulas')


# ---------- Validação de documentos (RH) ----------

def _aprovar(request, documento, empresa, descricao, pode_analisar):
    if not pode_analisar:
        messages.error(request, 'A documentação desta prestadora não está em análise.')
        return redirect('terceiros:detalhe_empresa', empresa_id=empresa.id)

    documento.status = 'aprovado'
    documento.motivo_rejeicao = ''
    documento.analisado_por = request.user
    documento.analisado_em = timezone.now()
    documento.save()

    messages.success(request, f'{descricao} aprovado.')
    return redirect('terceiros:detalhe_empresa', empresa_id=empresa.id)


def _rejeitar(request, documento, empresa, descricao, pode_analisar):
    if not pode_analisar:
        messages.error(request, 'A documentação desta prestadora não está em análise.')
        return redirect('terceiros:detalhe_empresa', empresa_id=empresa.id)

    form = RejeitarDocumentoTerceiroForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Informe o motivo da rejeição.')
        return redirect('terceiros:detalhe_empresa', empresa_id=empresa.id)

    documento.status = 'rejeitado'
    documento.motivo_rejeicao = form.cleaned_data['motivo']
    documento.analisado_por = request.user
    documento.analisado_em = timezone.now()
    documento.save()

    messages.success(request, f'{descricao} rejeitado. A prestadora poderá reenviar.')
    return redirect('terceiros:detalhe_empresa', empresa_id=empresa.id)


def _reabrir_envio_se_estava_em_validacao(empresa):
    """
    Volta para a prestadora reenviar -- mesmo caminho do fluxo CLT. Se o lote nem
    tinha sido fechado ainda, não há status para desfazer.
    """
    if empresa.status == 'em_validacao':
        empresa.status = 'aguardando_assinatura'
        empresa.save()


@login_required
@papel_required('rh')
def aprovar_documento_view(request, documento_id):
    documento = get_object_or_404(
        DocumentoColaborador.objects.select_related('colaborador__empresa'), id=documento_id
    )

    # Gancho futuro da catraca: quando a integração existir, é aqui que a selfie
    # aprovada vira cadastro facial e preenche `documento.colaborador.id_catraca`
    # -- só isso. Nada de digital (essa é do ponto, e só para CLT) e nada de
    # marcação, jornada ou horário: terceiro não bate ponto.

    empresa = documento.colaborador.empresa
    return _aprovar(
        request, documento, empresa,
        f'{documento.get_tipo_display()} de {documento.colaborador.nome}',
        pode_analisar=_pode_analisar_documento_de(documento, empresa),
    )


@login_required
@papel_required('rh')
def rejeitar_documento_view(request, documento_id):
    documento = get_object_or_404(
        DocumentoColaborador.objects.select_related('colaborador__empresa'), id=documento_id
    )
    empresa = documento.colaborador.empresa

    resposta = _rejeitar(
        request, documento, empresa, documento.get_tipo_display(),
        pode_analisar=_pode_analisar_documento_de(documento, empresa),
    )
    if documento.status == 'rejeitado':
        # Só o que trava o onboarding reabre a etapa de envio: um holerite
        # reprovado é rotina mensal e não devolve a empresa para trás.
        if documento.tipo in DocumentoColaborador.TIPOS_OBRIGATORIOS_ONBOARDING:
            _reabrir_envio_se_estava_em_validacao(empresa)
    return resposta


@login_required
@papel_required('rh')
def aprovar_documento_empresa_view(request, documento_id):
    documento = get_object_or_404(
        DocumentoEmpresaTerceira.objects.select_related('empresa'), id=documento_id
    )
    return _aprovar(
        request, documento, documento.empresa,
        f'{documento.get_tipo_display()} {documento.competencia_display}',
        pode_analisar=_pode_analisar_mensais(documento.empresa),
    )


@login_required
@papel_required('rh')
def rejeitar_documento_empresa_view(request, documento_id):
    documento = get_object_or_404(
        DocumentoEmpresaTerceira.objects.select_related('empresa'), id=documento_id
    )
    return _rejeitar(
        request, documento, documento.empresa,
        f'{documento.get_tipo_display()} {documento.competencia_display}',
        pode_analisar=_pode_analisar_mensais(documento.empresa),
    )


@login_required
@papel_required('rh')
def validar_empresa_view(request, empresa_id):
    empresa = get_object_or_404(EmpresaTerceira, id=empresa_id)

    if empresa.status != 'em_validacao':
        messages.error(request, 'A empresa precisa estar em validação para ser ativada.')
    elif not empresa.documentos_todos_aprovados():
        messages.error(request, 'Ainda há documentos pendentes ou rejeitados. Revise antes de validar.')
    else:
        empresa.status = 'ativo'
        empresa.save()
        messages.success(request, f'{empresa.nome_curto} validada e ativada com sucesso.')

    return redirect('terceiros:detalhe_empresa', empresa_id=empresa.id)


# ---------- Portal do Prestador (responsável terceiro) ----------
#
# Três áreas, cada uma um item da sidebar de `terceiros/base_terceiro.html`:
# "Minha Empresa" (cadastro da prestadora e do representante legal),
# "Documentação Mensal" (FGTS/DCTFWeb) e "Funcionários" (equipe e os documentos
# de cada pessoa). `painel_view` é o "Início", que só orienta e aponta para elas.
#
# Toda consulta daqui sai de `request.responsavel.empresa`, preenchido pelo
# decorator: é o que garante que um responsável nunca alcance dados de outra
# prestadora, nem trocando um id na URL.


def _equipe_editavel(empresa):
    """
    Antes do contrato não há equipe para cadastrar, então também não há o que
    editar ou remover. Depois disso o responsável manda na própria lista --
    inclusive com a empresa já ativa, porque prestadora troca de gente.
    """
    return empresa.status not in ('pendente_cadastro', 'aguardando_contrato')


@login_required
@responsavel_required
def painel_view(request):
    responsavel = request.responsavel
    empresa = responsavel.empresa

    colaboradores = list(empresa.colaboradores.prefetch_related('documentos').all())

    return render(request, 'terceiros/painel.html', {
        'responsavel': responsavel,
        'empresa': empresa,
        'contrato': empresa.contrato_atual(),
        'total_colaboradores': len(colaboradores),
        'colaboradores_incompletos': sum(
            1 for colaborador in colaboradores if not colaborador.documentacao_completa()
        ),
        'dados_completos': responsavel.dados_completos(),
        'documentos_rejeitados': DocumentoColaborador.objects.filter(
            colaborador__empresa=empresa, status='rejeitado'
        ).select_related('colaborador'),
        'documentos_empresa_rejeitados': DocumentoEmpresaTerceira.objects.filter(
            empresa=empresa, status='rejeitado'
        ),
    })


@login_required
@responsavel_required
def minha_empresa_view(request):
    """
    Cadastro da prestadora e do representante legal, na mesma tela: são os dois
    lados do que o contrato precisa, e o responsável responde pelos dois.

    Um formulário só, um botão só -- por isso os dois `is_valid()` são avaliados
    antes de qualquer `save()`: com um dos lados inválido nada é gravado, e a
    tela volta com os dois preenchidos como o usuário deixou.
    """
    responsavel = request.responsavel
    empresa = responsavel.empresa

    if request.method == 'POST':
        form_empresa = DadosEmpresaForm(request.POST, instance=empresa)
        form_responsavel = DadosResponsavelForm(request.POST, instance=responsavel)

        empresa_ok = form_empresa.is_valid()
        responsavel_ok = form_responsavel.is_valid()

        if empresa_ok and responsavel_ok:
            form_empresa.save()
            responsavel = form_responsavel.save()

            # Mesma transição de sempre: completar os dados do representante é o
            # que libera o RH a gerar o contrato.
            if responsavel.dados_completos() and empresa.status == 'pendente_cadastro':
                empresa.status = 'aguardando_contrato'
                empresa.save()
                messages.success(
                    request,
                    'Cadastro completo. O RH da Sotrigo foi liberado para gerar o contrato.',
                )
                return redirect('terceiros:painel')

            messages.success(request, 'Dados atualizados.')
            return redirect('terceiros:minha_empresa')
    else:
        form_empresa = DadosEmpresaForm(instance=empresa)
        form_responsavel = DadosResponsavelForm(instance=responsavel)

    return render(request, 'terceiros/minha_empresa.html', {
        'form_empresa': form_empresa,
        'form_responsavel': form_responsavel,
        'responsavel': responsavel,
        'empresa': empresa,
        'contrato': empresa.contrato_atual(),
    })


@login_required
@responsavel_required
def documentacao_mensal_view(request):
    """
    Obrigações mensais da prestadora: envio de FGTS/DCTFWeb e o histórico do que
    já foi entregue, competência a competência.

    O histórico traz junto os holerites da equipe (`competencias_mensais`)
    porque, na leitura do responsável, isso é um bloco só: "o que foi entregue
    no mês tal". O envio do holerite em si é por pessoa, na tela do colaborador.
    """
    empresa = request.responsavel.empresa

    if request.method == 'POST':
        form = EnviarDocumentoEmpresaForm(request.POST, request.FILES)
        if form.is_valid():
            documento = DocumentoEmpresaTerceira.registrar_envio(
                empresa=empresa,
                tipo=form.cleaned_data['tipo'],
                arquivo=form.cleaned_data['arquivo'],
                enviado_por=request.user,
                competencia_mes=form.cleaned_data['competencia_mes'],
                competencia_ano=form.cleaned_data['competencia_ano'],
            )
            messages.success(
                request,
                f'{documento.get_tipo_display()} de {documento.competencia_display} enviado.',
            )
            return redirect('terceiros:documentacao_mensal')
    else:
        tipo = request.GET.get('tipo')
        form = EnviarDocumentoEmpresaForm(initial={'tipo': tipo} if tipo else None)

    return render(request, 'terceiros/documentacao_mensal.html', {
        'form': form,
        'empresa': empresa,
        'competencias': empresa.competencias_mensais(),
    })


@login_required
@responsavel_required
def funcionarios_view(request):
    """
    A equipe alocada na Sotrigo: quem está cadastrado, o que falta de documento
    de cada um e o botão que fecha a etapa de documentação.
    """
    empresa = request.responsavel.empresa

    resumo_equipe = [
        {
            'colaborador': colaborador,
            'tipos_pendentes': colaborador.tipos_pendentes(),
            'ordens_servico': colaborador.ordens_servico(),
        }
        for colaborador in empresa.colaboradores.prefetch_related('documentos').all()
    ]

    return render(request, 'terceiros/funcionarios.html', {
        'empresa': empresa,
        'resumo_equipe': resumo_equipe,
        'equipe_editavel': _equipe_editavel(empresa),
        'pode_enviar_para_validacao': empresa.pode_enviar_para_validacao(),
    })


@login_required
@responsavel_required
def cadastrar_colaborador_view(request):
    empresa = request.responsavel.empresa

    if not _equipe_editavel(empresa):
        messages.error(request, 'Aguarde o contrato ser gerado pelo RH antes de cadastrar a equipe.')
        return redirect('terceiros:funcionarios')

    if request.method == 'POST':
        form = ColaboradorTerceiroForm(request.POST, empresa=empresa)
        if form.is_valid():
            colaborador = form.save(commit=False)
            colaborador.empresa = empresa
            colaborador.save()
            messages.success(request, f'{colaborador.nome} cadastrado. Agora envie os documentos dele.')
            return redirect('terceiros:detalhe_colaborador', colaborador_id=colaborador.id)
    else:
        form = ColaboradorTerceiroForm(empresa=empresa)

    return render(request, 'terceiros/cadastrar_colaborador.html', {
        'form': form,
        'empresa': empresa,
    })


@login_required
@responsavel_required
def editar_colaborador_view(request, colaborador_id):
    empresa = request.responsavel.empresa
    # Igual ao detalhe: filtrar por empresa é o que impede alcançar colaborador
    # de outra prestadora trocando o id na URL.
    colaborador = get_object_or_404(ColaboradorTerceiro, id=colaborador_id, empresa=empresa)

    if not _equipe_editavel(empresa):
        messages.error(request, 'Aguarde o contrato ser gerado pelo RH antes de mexer na equipe.')
        return redirect('terceiros:funcionarios')

    if request.method == 'POST':
        form = ColaboradorTerceiroForm(request.POST, instance=colaborador, empresa=empresa)
        if form.is_valid():
            colaborador = form.save()
            messages.success(request, f'Dados de {colaborador.nome} atualizados.')
            return redirect('terceiros:funcionarios')
    else:
        form = ColaboradorTerceiroForm(instance=colaborador, empresa=empresa)

    return render(request, 'terceiros/cadastrar_colaborador.html', {
        'form': form,
        'empresa': empresa,
        'colaborador': colaborador,
    })


@login_required
@responsavel_required
def remover_colaborador_view(request, colaborador_id):
    empresa = request.responsavel.empresa
    colaborador = get_object_or_404(ColaboradorTerceiro, id=colaborador_id, empresa=empresa)

    if request.method != 'POST':
        return redirect('terceiros:funcionarios')

    if not _equipe_editavel(empresa):
        messages.error(request, 'Aguarde o contrato ser gerado pelo RH antes de mexer na equipe.')
        return redirect('terceiros:funcionarios')

    nome = colaborador.nome
    # O cascade apaga as linhas de documento, mas não os arquivos: eles ficariam
    # órfãos em media/. Apaga do disco só depois que o delete do banco passou,
    # senão um erro na transação deixaria arquivo sumido com a linha viva.
    arquivos = [documento.arquivo for documento in colaborador.documentos.all() if documento.arquivo]
    colaborador.delete()
    for arquivo in arquivos:
        arquivo.delete(save=False)

    messages.success(request, f'{nome} foi removido da equipe.')
    return redirect('terceiros:funcionarios')


@login_required
@responsavel_required
def detalhe_colaborador_view(request, colaborador_id):
    """Uma pessoa da equipe: tudo que ela já entregou, e o envio de um documento novo."""
    empresa = request.responsavel.empresa
    # O filtro por empresa é o que impede um responsável de alcançar
    # colaborador de outra prestadora trocando o id na URL.
    colaborador = get_object_or_404(ColaboradorTerceiro, id=colaborador_id, empresa=empresa)

    if request.method == 'POST':
        form = EnviarDocumentoColaboradorForm(request.POST, request.FILES)
        if form.is_valid():
            DocumentoColaborador.registrar_envio(
                colaborador=colaborador,
                tipo=form.cleaned_data['tipo'],
                arquivo=form.cleaned_data['arquivo'],
                enviado_por=request.user,
                competencia_mes=form.cleaned_data.get('competencia_mes'),
                competencia_ano=form.cleaned_data.get('competencia_ano'),
            )
            messages.success(request, 'Documento enviado com sucesso.')
            return redirect('terceiros:detalhe_colaborador', colaborador_id=colaborador.id)
    else:
        tipo = request.GET.get('tipo')
        form = EnviarDocumentoColaboradorForm(initial={'tipo': tipo} if tipo else None)

    return render(request, 'terceiros/detalhe_colaborador.html', {
        'form': form,
        'colaborador': colaborador,
        'empresa': empresa,
        'documentos_onboarding': colaborador.documentos_de_onboarding(),
        'ordens_servico': colaborador.ordens_servico(),
        'holerites': colaborador.holerites(),
        'tipos_pendentes': colaborador.tipos_pendentes(),
        'tipo_preselecionado': bool(request.GET.get('tipo')),
        'tipos_mensais': DocumentoColaborador.TIPOS_MENSAIS,
        'tipos_multiplos': DocumentoColaborador.TIPOS_MULTIPLOS_LIVRES,
    })


@login_required
@responsavel_required
def enviar_para_validacao_view(request):
    empresa = request.responsavel.empresa

    if empresa.status != 'aguardando_assinatura':
        messages.error(request, 'Esta etapa não está aberta agora.')
    elif not empresa.pode_enviar_para_validacao():
        messages.error(
            request,
            'Cadastre pelo menos um colaborador e envie a documentação completa de cada um '
            '(contrato, documento pessoal, selfie, ASO, EPI e ao menos uma ordem de serviço) '
            'antes de mandar para o RH.',
        )
    else:
        empresa.status = 'em_validacao'
        empresa.save()
        messages.success(request, 'Documentação enviada para validação do RH da Sotrigo.')

    return redirect('terceiros:funcionarios')



# ---------- Downloads com verificação de permissão ----------

def _pode_ver_empresa(user, empresa):
    """
    RH vê tudo; o responsável vê só a própria empresa. Os arquivos nunca são
    linkados por `.url` nos templates -- todo download passa por aqui.
    """
    responsavel = responsavel_terceiro_de(user)
    if responsavel is not None:
        return responsavel.empresa_id == empresa.id

    usuario = usuario_de(user)
    if usuario is None:
        return False
    return usuario.papel == 'rh' or usuario.acesso_rh


@login_required
def baixar_contrato_view(request, contrato_id):
    contrato = get_object_or_404(
        ContratoTerceiro.objects.select_related('empresa'), id=contrato_id
    )

    if not _pode_ver_empresa(request.user, contrato.empresa):
        messages.error(request, 'Você não tem permissão para acessar este contrato.')
        return redirect('dashboard')

    if not contrato.arquivo_gerado:
        messages.error(request, 'Este contrato ainda não tem PDF gerado.')
        return redirect('dashboard')

    return FileResponse(contrato.arquivo_gerado.open('rb'), filename=contrato.nome_arquivo)


@login_required
def ver_documento_view(request, documento_id):
    documento = get_object_or_404(
        DocumentoColaborador.objects.select_related('colaborador__empresa'), id=documento_id
    )

    if not _pode_ver_empresa(request.user, documento.colaborador.empresa):
        messages.error(request, 'Você não tem permissão para acessar este documento.')
        return redirect('dashboard')

    return FileResponse(documento.arquivo.open('rb'), filename=documento.nome_arquivo)


@login_required
def ver_documento_empresa_view(request, documento_id):
    documento = get_object_or_404(
        DocumentoEmpresaTerceira.objects.select_related('empresa'), id=documento_id
    )

    if not _pode_ver_empresa(request.user, documento.empresa):
        messages.error(request, 'Você não tem permissão para acessar este documento.')
        return redirect('dashboard')

    return FileResponse(documento.arquivo.open('rb'), filename=documento.nome_arquivo)
