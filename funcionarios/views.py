import io
import secrets
from datetime import timedelta

import requests
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.forms import SetPasswordForm
from django.contrib import messages
from django.core.cache import cache
from django.db.models import Q, Count
from django.core.paginator import Paginator
from django.urls import reverse
from django.utils import timezone
from django.template.loader import render_to_string
from django.http import HttpResponse, FileResponse
from django.core.files.base import ContentFile
from xhtml2pdf import pisa

from .decorators import papel_required
from .models import Usuario, Funcionario, Documento, TipoDocumento, SolicitacaoFerias, AvisoFerias
from .forms import (
    MeuPerfilForm, FotoPerfilForm, EnviarDocumentoForm, FuncionarioForm, PreCadastroFuncionarioForm,
    DadosCadastraisForm, SolicitarFeriasForm, ProporOutraDataForm, SugerirAlteracaoForm, AvisoFeriasForm,
    RejeitarDocumentoForm,
)
from .utils import formatar_cpf, formatar_moeda, salario_por_extenso, data_por_extenso, data_curta_por_extenso

# Só pra mostrar/atribuir a jornada de ponto na ficha do funcionário -- a
# atribuição em si continua sendo feita pela view do app `ponto`.
from ponto.forms import FuncionarioJornadaForm, JornadaOpcionalForm
from ponto.models import AjustePonto, FuncionarioJornada
from ponto.calculos import calcular_periodo, situacao_do_dia


LIMITE_DIAS_ALERTA_FERIAS = 60

STATUS_ONBOARDING = ['pendente_documentos', 'aguardando_contrato', 'aguardando_assinatura', 'em_validacao']

LIMITE_TENTATIVAS_LOGIN_USUARIO = 5
LIMITE_TENTATIVAS_LOGIN_IP = 20
JANELA_BLOQUEIO_LOGIN = 15 * 60  # segundos

MENSAGEM_BLOQUEIO_LOGIN = 'Muitas tentativas de login. Tente novamente em alguns minutos.'
MENSAGEM_CREDENCIAL_INVALIDA = 'Usuário ou senha inválidos.'
MENSAGEM_RECAPTCHA_INVALIDO = 'Não foi possível confirmar o reCAPTCHA. Marque a caixa novamente e envie o formulário.'


# ---------- Autenticação ----------

def _recaptcha_valido(request):
    """Confere o desafio do reCAPTCHA v2 na API do Google.

    Sem RECAPTCHA_SECRET_KEY configurada (nenhum site cadastrado ainda), o
    desafio não é exigido. Se o Google ficar inacessível, deixa passar --
    uma instabilidade externa não pode travar o login de todo mundo -- mas
    uma reprovação explícita da API bloqueia o envio.
    """
    secret = settings.RECAPTCHA_SECRET_KEY
    if not secret:
        return True

    token = request.POST.get('g-recaptcha-response', '')
    if not token:
        return False

    try:
        resposta = requests.post(
            'https://www.google.com/recaptcha/api/siteverify',
            data={
                'secret': secret,
                'response': token,
                'remoteip': request.META.get('REMOTE_ADDR'),
            },
            timeout=5,
        )
        return resposta.json().get('success', False)
    except requests.RequestException:
        return True


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    contexto = {'recaptcha_site_key': settings.RECAPTCHA_SITE_KEY}

    if request.method == 'POST':
        username = request.POST.get('username', '')
        password = request.POST.get('password')
        ip = request.META.get('REMOTE_ADDR', 'desconhecido')

        chave_usuario = f'login_falhas:usuario:{username}'
        chave_ip = f'login_falhas:ip:{ip}'

        if cache.get(chave_usuario, 0) >= LIMITE_TENTATIVAS_LOGIN_USUARIO or \
           cache.get(chave_ip, 0) >= LIMITE_TENTATIVAS_LOGIN_IP:
            contexto['erro'] = MENSAGEM_BLOQUEIO_LOGIN
            return render(request, 'funcionarios/login.html', contexto)

        if not _recaptcha_valido(request):
            contexto['erro'] = MENSAGEM_RECAPTCHA_INVALIDO
            return render(request, 'funcionarios/login.html', contexto)

        user = authenticate(request, username=username, password=password)

        if user is not None:
            cache.delete(chave_usuario)
            login(request, user)
            return redirect('dashboard')
        else:
            cache.set(chave_usuario, cache.get(chave_usuario, 0) + 1, JANELA_BLOQUEIO_LOGIN)
            cache.set(chave_ip, cache.get(chave_ip, 0) + 1, JANELA_BLOQUEIO_LOGIN)
            contexto['erro'] = MENSAGEM_CREDENCIAL_INVALIDA
            return render(request, 'funcionarios/login.html', contexto)

    return render(request, 'funcionarios/login.html', contexto)


def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def trocar_senha_view(request):
    try:
        usuario = request.user.usuario
    except Usuario.DoesNotExist:
        return redirect('dashboard')

    if request.method == 'POST':
        form = SetPasswordForm(request.user, request.POST)
        if form.is_valid():
            form.save()
            if usuario.senha_provisoria:
                usuario.senha_provisoria = False
                usuario.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, 'Senha alterada com sucesso.')
            return redirect('dashboard')
    else:
        form = SetPasswordForm(request.user)

    return render(request, 'funcionarios/trocar_senha.html', {
        'form': form,
        'senha_provisoria': usuario.senha_provisoria,
    })


# ---------- Dashboard ----------

@login_required
def dashboard_view(request):
    try:
        usuario = request.user.usuario
    except Usuario.DoesNotExist:
        usuario = None

    if usuario is None:
        return render(request, 'funcionarios/sem_perfil.html')

    if usuario.papel == 'colaborador':
        funcionario = usuario.funcionario
        tipos_iniciais = funcionario.tipos_documentos_iniciais()
        tipos_pendentes = funcionario.tipos_documentos_nunca_enviados()
        total_obrigatorios = len(tipos_iniciais)
        documentos_rejeitados = funcionario.documentos.filter(status='rejeitado')

        solicitacao_ferias = funcionario.solicitacoes_ferias.order_by('-criado_em').first()
        tem_ativa = funcionario.solicitacoes_ferias.exclude(status__in=['aprovada', 'adiada']).exists()
        aviso_pendente = funcionario.aviso_ferias_pendente_acao()

        hoje = timezone.now().date()
        resultado_ponto = calcular_periodo(funcionario, hoje - timedelta(days=6), hoje)
        for dia in resultado_ponto['dias']:
            texto, cor, pode_solicitar = situacao_do_dia(dia, hoje)
            dia['situacao_texto'] = texto
            dia['situacao_cor'] = cor

        return render(request, 'funcionarios/colaborador/dashboard.html', {
            'funcionario': funcionario,
            'tipos_pendentes': tipos_pendentes,
            'total_obrigatorios': total_obrigatorios,
            'total_enviados': total_obrigatorios - len(tipos_pendentes),
            'documentos_rejeitados': documentos_rejeitados,
            'solicitacao_ferias': solicitacao_ferias,
            'hoje': hoje,
            'tem_ativa': tem_ativa,
            'aviso_pendente': aviso_pendente,
            'ultimos_dias_ponto': resultado_ponto['dias'],
        })

    return dashboard_rh_view(request)


@login_required
@papel_required('rh', 'gestor')
def dashboard_rh_view(request):
    usuario = request.user.usuario
    funcionario_atual = usuario.funcionario

    if usuario.papel == 'rh' or usuario.acesso_rh:
        funcionarios_do_escopo = Funcionario.objects.all()
    else:
        funcionarios_do_escopo = Funcionario.objects.filter(setor=usuario.funcionario.setor)

    agora = timezone.localtime(timezone.now())
    hoje = agora.date()

    funcionarios_ativos = funcionarios_do_escopo.filter(status='ativo')
    total_funcionarios = funcionarios_ativos.count()
    admitidos_no_mes = funcionarios_ativos.filter(
        data_admissao__year=hoje.year, data_admissao__month=hoje.month
    ).count()

    cadastros_pendentes_qs = funcionarios_do_escopo.filter(status__in=STATUS_ONBOARDING)
    cadastros_pendentes = cadastros_pendentes_qs.count()
    limite_parado = hoje - timedelta(days=7)
    cadastros_pendentes_antigos = cadastros_pendentes_qs.filter(data_admissao__lte=limite_parado).count()

    funcionarios_por_setor = funcionarios_ativos.values('setor__nome').annotate(total=Count('id')).order_by('-total')
    maior_total_setor = max([item['total'] for item in funcionarios_por_setor], default=0)

    ferias_gestor_qs = SolicitacaoFerias.objects.filter(
        status='pendente_gestor', funcionario__in=funcionarios_do_escopo
    ).order_by('criado_em')
    ferias_aguardando_gestor = ferias_gestor_qs.count()
    mais_antiga_gestor = ferias_gestor_qs.first()
    dias_mais_antiga_gestor = (hoje - mais_antiga_gestor.criado_em.date()).days if mais_antiga_gestor else None

    ferias_rh_qs = SolicitacaoFerias.objects.filter(
        status='pendente_rh', funcionario__in=funcionarios_do_escopo
    ).select_related('funcionario').order_by('atualizado_em')
    ferias_aguardando_rh = ferias_rh_qs.count()

    # ---- Feed "Precisa da sua ação": junta tudo que só o RH consegue resolver ----
    itens_acao = []

    for f in funcionarios_do_escopo.filter(status='em_validacao'):
        total_docs = f.documentos.count()
        sufixo_docs = '' if total_docs == 1 else 's'
        itens_acao.append({
            'titulo': f'{f.nome} — validar cadastro',
            'subtitulo': f'{total_docs} documento{sufixo_docs} enviados, aguardando análise',
            'url': reverse('detalhe_funcionario', args=[f.id]),
        })

    for s in ferias_rh_qs:
        itens_acao.append({
            'titulo': f'{s.funcionario.nome} — concluir férias',
            'subtitulo': f'aprovada pelo gestor em {timezone.localtime(s.atualizado_em).strftime("%d/%m")}',
            'url': reverse('concluir_ferias'),
        })

    avisos_sem_solicitacao = AvisoFerias.objects.filter(
        funcionario__in=funcionarios_do_escopo, solicitacoes_geradas__isnull=True,
    ).select_related('funcionario').order_by('data_prevista')
    for aviso in avisos_sem_solicitacao:
        dias = (aviso.data_prevista - hoje).days
        if dias >= 0:
            titulo_aviso = f'{aviso.funcionario.nome} — férias vencem em {dias} dia{"s" if dias != 1 else ""}'
        else:
            titulo_aviso = f'{aviso.funcionario.nome} — férias venceram há {abs(dias)} dia{"s" if abs(dias) != 1 else ""}'
        itens_acao.append({
            'titulo': titulo_aviso,
            'subtitulo': 'sem solicitação registrada',
            'url': reverse('rh_vencimento_ferias'),
        })

    for f in funcionarios_do_escopo.filter(status='aguardando_assinatura'):
        contrato = f.documentos.filter(tipo_documento__eh_contrato=True).order_by('-data_upload').first()
        if contrato:
            dias_gerado = (hoje - timezone.localtime(contrato.data_upload).date()).days
            subtitulo = f'gerado há {dias_gerado} dia{"s" if dias_gerado != 1 else ""}'
        else:
            subtitulo = 'aguardando geração'
        itens_acao.append({
            'titulo': f'{f.nome} — contrato sem assinatura',
            'subtitulo': subtitulo,
            'url': reverse('detalhe_funcionario', args=[f.id]),
        })

    ajustes_pendentes_count = AjustePonto.objects.filter(
        status='pendente', funcionario__in=funcionarios_do_escopo
    ).count()
    if ajustes_pendentes_count:
        itens_acao.append({
            'titulo': f'{ajustes_pendentes_count} correç{"ão" if ajustes_pendentes_count == 1 else "ões"} de ponto para analisar',
            'subtitulo': 'pedidas pelos colaboradores',
            'url': reverse('ponto:ajustes_pendentes'),
        })

    if agora.hour < 12:
        saudacao = 'Bom dia'
    elif agora.hour < 18:
        saudacao = 'Boa tarde'
    else:
        saudacao = 'Boa noite'

    return render(request, 'funcionarios/rh/dashboard.html', {
        'saudacao': saudacao,
        'nome_usuario': funcionario_atual.primeiro_nome,
        'data_extenso': data_curta_por_extenso(hoje),
        'total_funcionarios': total_funcionarios,
        'admitidos_no_mes': admitidos_no_mes,
        'cadastros_pendentes': cadastros_pendentes,
        'cadastros_pendentes_antigos': cadastros_pendentes_antigos,
        'ferias_aguardando_gestor': ferias_aguardando_gestor,
        'dias_mais_antiga_gestor': dias_mais_antiga_gestor,
        'ferias_aguardando_rh': ferias_aguardando_rh,
        'funcionarios_por_setor': funcionarios_por_setor,
        'maior_total_setor': maior_total_setor,
        'itens_acao': itens_acao[:5],
        'total_itens_acao': len(itens_acao),
        'avisos_vencendo_30_dias': avisos_sem_solicitacao.filter(data_prevista__lte=hoje + timedelta(days=30)).count(),
    })


# ---------- Meu Perfil ----------

@login_required
def meu_perfil_view(request):
    try:
        funcionario = request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return render(request, 'funcionarios/sem_perfil.html')

    tipos_documentos_filho = TipoDocumento.objects.filter(condicao='filho_menor_idade', obrigatorio=True)

    if request.method == 'POST':
        form = MeuPerfilForm(request.POST, request.FILES, instance=funcionario)
        if form.is_valid():
            form.save()

            for tipo in tipos_documentos_filho:
                arquivo = request.FILES.get(f'documento_filho_{tipo.id}')
                if arquivo:
                    Documento.substituir_ou_criar(
                        funcionario=funcionario,
                        tipo_documento=tipo,
                        arquivo=arquivo,
                        enviado_por=request.user,
                    )

            avancar_status_por_documentos(funcionario)
            messages.success(request, 'Perfil atualizado com sucesso.')

            return redirect('meu_perfil')
    else:
        form = MeuPerfilForm(instance=funcionario)

    tipos_documentos_filho_enviados = set(
        funcionario.documentos.filter(
            tipo_documento__in=tipos_documentos_filho
        ).values_list('tipo_documento_id', flat=True)
    )

    return render(request, 'funcionarios/meu_perfil.html', {
        'form': form,
        'foto_form': FotoPerfilForm(instance=funcionario),
        'funcionario': funcionario,
        'tipos_documentos_filho': tipos_documentos_filho,
        'tipos_documentos_filho_enviados': tipos_documentos_filho_enviados,
    })


@login_required
def atualizar_foto_view(request):
    try:
        funcionario = request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return render(request, 'funcionarios/sem_perfil.html')

    if request.method == 'POST':
        form = FotoPerfilForm(request.POST, request.FILES, instance=funcionario)
        if form.is_valid():
            form.save()
            messages.success(request, 'Foto atualizada com sucesso.')
        else:
            messages.error(request, 'Não foi possível atualizar a foto. Verifique o arquivo enviado.')

    return redirect('meu_perfil')


# ---------- Documentos ----------

def avancar_status_por_documentos(funcionario):
    if funcionario.status == 'pendente_documentos' and funcionario.documentos_iniciais_completos():
        funcionario.status = 'aguardando_contrato'
        funcionario.save()
    elif funcionario.status == 'aguardando_assinatura' and funcionario.todos_documentos_enviados():
        funcionario.status = 'em_validacao'
        funcionario.save()


@login_required
def enviar_documento_view(request):
    try:
        funcionario = request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return render(request, 'funcionarios/sem_perfil.html')

    if request.method == 'POST':
        form = EnviarDocumentoForm(request.POST, request.FILES, funcionario=funcionario)
        if form.is_valid():
            Documento.substituir_ou_criar(
                funcionario=funcionario,
                tipo_documento=form.cleaned_data['tipo_documento'],
                arquivo=form.cleaned_data['arquivo'],
                enviado_por=request.user,
            )
            avancar_status_por_documentos(funcionario)
            messages.success(request, 'Documento enviado com sucesso.')

            if request.POST.get('origem') == 'meus_documentos':
                return redirect('meus_documentos')
            elif request.POST.get('origem') == 'assinatura_gov':
                return redirect('minhas_assinaturas')
            return redirect('enviar_documento')
        tipo_preselecionado = False
    else:
        tipo_id = request.GET.get('tipo')
        form = EnviarDocumentoForm(funcionario=funcionario, initial={'tipo_documento': tipo_id} if tipo_id else None)
        tipo_preselecionado = bool(tipo_id)

    return render(request, 'funcionarios/enviar_documento.html', {
        'form': form,
        'tipo_preselecionado': tipo_preselecionado,
    })


@login_required
def meus_documentos_view(request):
    try:
        funcionario = request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return render(request, 'funcionarios/sem_perfil.html')

    documentos = funcionario.documentos.exclude(tipo_documento__eh_contrato=True).order_by('-data_upload')
    contrato_documento = funcionario.documentos.filter(tipo_documento__eh_contrato=True).first()

    tipos_obrigatorios = funcionario.tipos_documentos_iniciais()
    tipos_pendentes = funcionario.tipos_documentos_nunca_enviados()
    total_obrigatorios = len(tipos_obrigatorios)
    total_enviados = total_obrigatorios - len(tipos_pendentes)
    tipos_opcionais = funcionario.tipos_documentos_opcionais()

    return render(request, 'funcionarios/meus_documentos.html', {
        'documentos': documentos,
        'tipos_pendentes': tipos_pendentes,
        'total_obrigatorios': total_obrigatorios,
        'total_enviados': total_enviados,
        'contrato_documento': contrato_documento,
        'tipos_opcionais': tipos_opcionais,
        'funcionario': funcionario,
    })


@login_required
def assinatura_gov_view(request):
    try:
        funcionario = request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return render(request, 'funcionarios/sem_perfil.html')

    contrato_documento = funcionario.documentos.filter(tipo_documento__eh_contrato=True).first()

    return render(request, 'funcionarios/assinatura_gov.html', {
        'contrato_documento': contrato_documento,
    })


@login_required
def ver_documento_view(request, documento_id):
    documento = get_object_or_404(Documento, id=documento_id)

    try:
        usuario = request.user.usuario
    except Usuario.DoesNotExist:
        messages.error(request, 'Você não tem permissão para acessar este documento.')
        return redirect('dashboard')

    eh_dono = usuario.funcionario_id == documento.funcionario_id
    eh_rh = usuario.papel == 'rh' or usuario.acesso_rh
    eh_gestor_do_setor = usuario.papel == 'gestor' and documento.funcionario.setor_id == usuario.funcionario.setor_id

    if not (eh_dono or eh_rh or eh_gestor_do_setor):
        messages.error(request, 'Você não tem permissão para acessar este documento.')
        return redirect('dashboard')

    return FileResponse(documento.arquivo.open('rb'), filename=documento.nome_arquivo)


# ---------- Funcionários (RH / Gestor) ----------

@login_required
@papel_required('rh', 'gestor')
def listar_funcionarios_view(request):
    usuario = request.user.usuario
    tem_acesso_total = usuario.papel == 'rh' or usuario.acesso_rh
    escopo = request.GET.get('escopo', 'todos') if tem_acesso_total else 'meu_setor'
    status_filtro = request.GET.get('status', 'ativo')
    busca = request.GET.get('busca', '').strip()

    if tem_acesso_total and escopo == 'todos':
        base_qs = Funcionario.objects.all()
    else:
        base_qs = Funcionario.objects.filter(setor=usuario.funcionario.setor)

    contagem_ativos = base_qs.filter(status='ativo').count()
    contagem_pendentes = base_qs.filter(status__in=STATUS_ONBOARDING).count()
    contagem_inativos = base_qs.filter(status='inativo').count()

    if status_filtro == 'pendente':
        funcionarios = base_qs.filter(status__in=STATUS_ONBOARDING)
    else:
        funcionarios = base_qs.filter(status=status_filtro)

    if busca:
        funcionarios = funcionarios.filter(
            Q(nome__icontains=busca) | Q(cpf__icontains=busca)
        )

    funcionarios = funcionarios.order_by('nome')

    paginator = Paginator(funcionarios, 50)
    numero_pagina = request.GET.get('page', 1)
    pagina = paginator.get_page(numero_pagina)

    intervalo_paginas = paginator.get_elided_page_range(
        pagina.number, on_each_side=2, on_ends=1
    )

    return render(request, 'funcionarios/rh/listar_funcionarios.html', {
        'pagina': pagina,
        'intervalo_paginas': intervalo_paginas,
        'status_filtro': status_filtro,
        'busca': busca,
        'contagem_ativos': contagem_ativos,
        'contagem_pendentes': contagem_pendentes,
        'contagem_inativos': contagem_inativos,
        'tem_acesso_total': tem_acesso_total,
        'escopo': escopo,
    })


@login_required
@papel_required('rh', 'gestor')
def cadastrar_funcionario_view(request):
    if request.method == 'POST':
        form = PreCadastroFuncionarioForm(request.POST)
        jornada_form = JornadaOpcionalForm(request.POST)
        if form.is_valid() and jornada_form.is_valid():
            funcionario = form.save(commit=False)
            funcionario.status = 'pendente_documentos'
            funcionario.save()

            horario = jornada_form.cleaned_data.get('horario')
            if horario:
                FuncionarioJornada.objects.create(
                    funcionario=funcionario,
                    data_inicio=funcionario.data_admissao or timezone.now().date(),
                    horario=horario,
                )

            senha_gerada = secrets.token_urlsafe(9)

            user = User.objects.create_user(
                username=funcionario.cpf,
                password=senha_gerada,
            )

            Usuario.objects.create(
                user=user,
                funcionario=funcionario,
                papel='colaborador',
                senha_provisoria=True,
            )

            return render(request, 'funcionarios/rh/senha_gerada.html', {
                'funcionario': funcionario,
                'username': user.username,
                'senha_gerada': senha_gerada,
            })
    else:
        form = PreCadastroFuncionarioForm()
        jornada_form = JornadaOpcionalForm()

    return render(request, 'funcionarios/rh/cadastrar_funcionario.html', {
        'form': form,
        'jornada_form': jornada_form,
    })


@login_required
@papel_required('rh', 'gestor')
def detalhe_funcionario_view(request, funcionario_id):
    usuario = request.user.usuario
    funcionario = get_object_or_404(Funcionario, id=funcionario_id)

    if usuario.papel == 'gestor' and not usuario.acesso_rh and funcionario.setor != usuario.funcionario.setor:
        messages.error(request, 'Você não tem permissão para ver este funcionário.')
        return redirect('listar_funcionarios')

    avancar_status_por_documentos(funcionario)

    documentos = funcionario.documentos.all().order_by('-data_upload')

    tipos_obrigatorios = funcionario.tipos_documentos_obrigatorios()
    # ordenado do mais antigo pro mais novo: se sobrar mais de um Documento do mesmo tipo
    # (dados legados de antes da substituição por upsert), o dict fica com o mais recente.
    documentos_por_tipo = {
        documento.tipo_documento_id: documento
        for documento in funcionario.documentos.order_by('data_upload')
    }
    checklist_documentos = [
        {'tipo': tipo, 'documento': documentos_por_tipo.get(tipo.id)}
        for tipo in tipos_obrigatorios
    ]
    total_obrigatorios = len(tipos_obrigatorios)
    total_enviados_obrigatorios = sum(
        1 for item in checklist_documentos if item['documento'] and item['documento'].status != 'rejeitado'
    )
    pode_validar = funcionario.status == 'em_validacao' and funcionario.documentos_todos_aprovados()

    return render(request, 'funcionarios/rh/detalhe_funcionario.html', {
        'funcionario': funcionario,
        'documentos': documentos,
        'checklist_documentos': checklist_documentos,
        'total_obrigatorios': total_obrigatorios,
        'total_enviados_obrigatorios': total_enviados_obrigatorios,
        'pode_validar': pode_validar,
        'form_rejeicao': RejeitarDocumentoForm(),
        'jornada_vigente': FuncionarioJornada.vigente_em(funcionario, timezone.now().date()),
        'jornada_form': FuncionarioJornadaForm(),
        'dados_form': DadosCadastraisForm(instance=funcionario),
    })


@login_required
@papel_required('rh', 'gestor')
def editar_dados_funcionario_view(request, funcionario_id):
    usuario = request.user.usuario
    funcionario = get_object_or_404(Funcionario, id=funcionario_id)

    if not (usuario.papel == 'rh' or usuario.acesso_rh):
        messages.error(request, 'Você não tem permissão para essa ação.')
        return redirect('detalhe_funcionario', funcionario_id=funcionario.id)

    if request.method == 'POST':
        form = DadosCadastraisForm(request.POST, instance=funcionario)
        if form.is_valid():
            form.save()
            messages.success(request, 'Dados atualizados com sucesso.')
        else:
            messages.error(request, 'Não foi possível atualizar os dados. Verifique os campos.')

    return redirect('detalhe_funcionario', funcionario_id=funcionario.id)


@login_required
@papel_required('rh', 'gestor')
def gerar_acesso_funcionario_view(request, funcionario_id):
    usuario = request.user.usuario
    funcionario = get_object_or_404(Funcionario, id=funcionario_id)

    if usuario.papel == 'gestor' and not usuario.acesso_rh and funcionario.setor != usuario.funcionario.setor:
        messages.error(request, 'Você não tem permissão para essa ação.')
        return redirect('listar_funcionarios')

    if hasattr(funcionario, 'usuario'):
        messages.error(request, 'Este funcionário já possui acesso ao sistema.')
        return redirect('detalhe_funcionario', funcionario_id=funcionario.id)

    if not funcionario.cpf:
        messages.error(request, 'Este funcionário não tem CPF cadastrado. Preencha o CPF antes de gerar o acesso.')
        return redirect('detalhe_funcionario', funcionario_id=funcionario.id)

    senha_gerada = settings.SENHA_PADRAO_ACESSO

    user = User.objects.create_user(
        username=funcionario.cpf,
        password=senha_gerada,
    )

    Usuario.objects.create(
        user=user,
        funcionario=funcionario,
        papel='colaborador',
        senha_provisoria=True,
    )

    return render(request, 'funcionarios/rh/senha_gerada.html', {
        'funcionario': funcionario,
        'username': user.username,
        'senha_gerada': senha_gerada,
    })


@login_required
@papel_required('rh', 'gestor')
def resetar_senha_funcionario_view(request, funcionario_id):
    usuario = request.user.usuario
    funcionario = get_object_or_404(Funcionario, id=funcionario_id)

    if usuario.papel == 'gestor' and not usuario.acesso_rh and funcionario.setor != usuario.funcionario.setor:
        messages.error(request, 'Você não tem permissão para essa ação.')
        return redirect('listar_funcionarios')

    if not hasattr(funcionario, 'usuario'):
        messages.error(request, 'Este funcionário ainda não tem acesso ao sistema.')
        return redirect('detalhe_funcionario', funcionario_id=funcionario.id)

    senha_gerada = secrets.token_urlsafe(9)
    funcionario.usuario.user.set_password(senha_gerada)
    funcionario.usuario.user.save()
    funcionario.usuario.senha_provisoria = True
    funcionario.usuario.save()

    return render(request, 'funcionarios/rh/senha_gerada.html', {
        'funcionario': funcionario,
        'username': funcionario.usuario.user.username,
        'senha_gerada': senha_gerada,
        'reset_senha': True,
    })


@login_required
@papel_required('rh')
def alternar_acesso_rh_view(request, funcionario_id):
    funcionario = get_object_or_404(Funcionario, id=funcionario_id)

    if not hasattr(funcionario, 'usuario'):
        messages.error(request, 'Este funcionário ainda não tem acesso ao sistema.')
        return redirect('detalhe_funcionario', funcionario_id=funcionario.id)

    usuario = funcionario.usuario
    usuario.acesso_rh = not usuario.acesso_rh
    usuario.save()

    if usuario.acesso_rh:
        messages.success(request, f'Acesso ao RH concedido para {funcionario.nome}.')
    else:
        messages.success(request, f'Acesso ao RH removido de {funcionario.nome}.')

    return redirect('detalhe_funcionario', funcionario_id=funcionario.id)


@login_required
@papel_required('rh')
def gerar_contrato_view(request, funcionario_id):
    funcionario = get_object_or_404(Funcionario, id=funcionario_id)

    if funcionario.status not in ('aguardando_contrato', 'aguardando_assinatura'):
        messages.error(request, 'O contrato só pode ser gerado depois que os documentos iniciais forem enviados.')
        return redirect('detalhe_funcionario', funcionario_id=funcionario.id)

    if not funcionario.dados_contrato_completos():
        messages.error(request, 'Preencha cargo, salário e endereço antes de gerar o contrato.')
        return redirect('detalhe_funcionario', funcionario_id=funcionario.id)

    contexto = {
        'funcionario': funcionario,
        'cpf_formatado': formatar_cpf(funcionario.cpf),
        'salario_formatado': formatar_moeda(funcionario.salario),
        'salario_extenso': salario_por_extenso(funcionario.salario),
        'eh_qualidade': funcionario.setor.usa_contrato_qualidade,
        'data_extenso': data_por_extenso(timezone.now().date()),
    }
    html = render_to_string('funcionarios/contratos/contrato_experiencia.html', contexto)

    buffer = io.BytesIO()
    pisa_status = pisa.CreatePDF(html, dest=buffer)
    if pisa_status.err:
        messages.error(request, 'Não foi possível gerar o contrato. Tente novamente.')
        return redirect('detalhe_funcionario', funcionario_id=funcionario.id)

    pdf_bytes = buffer.getvalue()
    nome_arquivo = f'contrato_{funcionario.cpf}.pdf'

    tipo_contrato, _ = TipoDocumento.objects.get_or_create(
        nome='Contrato de Trabalho',
        defaults={'obrigatorio': True, 'eh_contrato': True, 'condicao': 'sempre'},
    )
    Documento.substituir_ou_criar(
        funcionario=funcionario,
        tipo_documento=tipo_contrato,
        arquivo=ContentFile(pdf_bytes, name=nome_arquivo),
        enviado_por=request.user,
    )

    funcionario.status = 'aguardando_assinatura'
    funcionario.save()

    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{nome_arquivo}"'
    return response


@login_required
@papel_required('rh', 'gestor')
def aprovar_documento_view(request, documento_id):
    usuario = request.user.usuario
    documento = get_object_or_404(Documento, id=documento_id)

    if not (usuario.papel == 'rh' or usuario.acesso_rh):
        messages.error(request, 'Você não tem permissão para essa ação.')
        return redirect('detalhe_funcionario', funcionario_id=documento.funcionario_id)

    if documento.funcionario.status != 'em_validacao':
        messages.error(request, 'Este documento só pode ser revisado quando o funcionário estiver em validação.')
        return redirect('detalhe_funcionario', funcionario_id=documento.funcionario_id)

    documento.status = 'aprovado'
    documento.motivo_rejeicao = ''
    documento.analisado_por = request.user
    documento.analisado_em = timezone.now()
    documento.save()

    messages.success(request, f'{documento.tipo_documento} aprovado.')
    return redirect('detalhe_funcionario', funcionario_id=documento.funcionario_id)


@login_required
@papel_required('rh', 'gestor')
def rejeitar_documento_view(request, documento_id):
    usuario = request.user.usuario
    documento = get_object_or_404(Documento, id=documento_id)

    if not (usuario.papel == 'rh' or usuario.acesso_rh):
        messages.error(request, 'Você não tem permissão para essa ação.')
        return redirect('detalhe_funcionario', funcionario_id=documento.funcionario_id)

    if documento.funcionario.status != 'em_validacao':
        messages.error(request, 'Este documento só pode ser revisado quando o funcionário estiver em validação.')
        return redirect('detalhe_funcionario', funcionario_id=documento.funcionario_id)

    form = RejeitarDocumentoForm(request.POST)
    if form.is_valid():
        documento.status = 'rejeitado'
        documento.motivo_rejeicao = form.cleaned_data['motivo']
        documento.analisado_por = request.user
        documento.analisado_em = timezone.now()
        documento.save()
        messages.success(request, f'{documento.tipo_documento} rejeitado. O colaborador poderá reenviar.')
    else:
        messages.error(request, 'Informe o motivo da rejeição.')

    return redirect('detalhe_funcionario', funcionario_id=documento.funcionario_id)


@login_required
@papel_required('rh', 'gestor')
def validar_funcionario_view(request, funcionario_id):
    usuario = request.user.usuario
    funcionario = get_object_or_404(Funcionario, id=funcionario_id)

    if not (usuario.papel == 'rh' or usuario.acesso_rh):
        messages.error(request, 'Você não tem permissão para essa ação.')
        return redirect('detalhe_funcionario', funcionario_id=funcionario.id)

    if funcionario.status != 'em_validacao':
        messages.error(request, 'O funcionário precisa estar em validação para ser ativado.')
    elif not funcionario.documentos_todos_aprovados():
        messages.error(request, 'Ainda há documentos obrigatórios pendentes ou rejeitados. Revise antes de validar.')
    else:
        funcionario.status = 'ativo'
        funcionario.save()
        messages.success(request, f'{funcionario.nome} validado e ativado com sucesso.')

    return redirect('detalhe_funcionario', funcionario_id=funcionario.id)


# ---------- Férias — Colaborador ----------

def _resumo_historico_ferias(solicitacao, hoje):
    """
    Rótulo curto + descrição pro histórico de "Minhas férias" -- usa uma
    linguagem mais compacta que `get_status_display()` (ex.: "Concluída" em
    vez de "Aprovada" quando já passou, "Alterada" em vez de "Gestor sugeriu
    alteração"), sem mudar o texto usado no resto do sistema.
    """
    if solicitacao.status == 'aprovada':
        if solicitacao.data_fim < hoje:
            return {
                'solicitacao': solicitacao,
                'rotulo': 'Concluída',
                'classe': 'aprovada',
                'descricao': f'concluída pelo RH em {timezone.localtime(solicitacao.atualizado_em).strftime("%d/%m/%Y")}',
            }
        return {
            'solicitacao': solicitacao,
            'rotulo': 'Aprovada',
            'classe': 'aprovada',
            'descricao': f'começa em {solicitacao.data_solicitada.strftime("%d/%m/%Y")}',
        }
    if solicitacao.status == 'sugestao_gestor':
        return {
            'solicitacao': solicitacao, 'rotulo': 'Alterada', 'classe': 'sugestao',
            'descricao': 'gestor sugeriu outra data',
        }
    if solicitacao.status == 'adiada':
        return {
            'solicitacao': solicitacao, 'rotulo': 'Adiada', 'classe': 'adiada',
            'descricao': 'adiada pelo colaborador',
        }
    if solicitacao.status == 'pendente_rh':
        return {
            'solicitacao': solicitacao, 'rotulo': 'Aguardando RH', 'classe': 'aguardando',
            'descricao': f'aprovada pelo gestor em {timezone.localtime(solicitacao.atualizado_em).strftime("%d/%m/%Y")}',
        }
    return {
        'solicitacao': solicitacao, 'rotulo': 'Aguardando Gestor', 'classe': 'aguardando',
        'descricao': f'enviada em {timezone.localtime(solicitacao.criado_em).strftime("%d/%m/%Y")}',
    }


@login_required
def solicitar_ferias_view(request):
    try:
        funcionario = request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return render(request, 'funcionarios/sem_perfil.html')

    solicitacoes = funcionario.solicitacoes_ferias.all().order_by('-criado_em')
    solicitacao_ativa = solicitacoes.exclude(status__in=['aprovada', 'adiada']).first()
    tem_ativa = solicitacao_ativa is not None
    aviso_pendente = funcionario.aviso_ferias_pendente_acao()
    hoje_ferias = timezone.now().date()
    historico = [_resumo_historico_ferias(s, hoje_ferias) for s in solicitacoes]

    if request.method == 'POST' and not tem_ativa:
        form = SolicitarFeriasForm(request.POST)
        if form.is_valid():
            solicitacao = form.save(commit=False)
            solicitacao.funcionario = funcionario
            if funcionario.eh_gestor or aviso_pendente:
                solicitacao.status = 'pendente_rh'
                if aviso_pendente:
                    solicitacao.aviso_origem = aviso_pendente
                solicitacao.save()
                messages.success(request, 'Solicitação de férias enviada diretamente ao RH.')
            else:
                solicitacao.save()
                messages.success(request, 'Solicitação de férias enviada ao seu gestor.')
            return redirect('solicitar_ferias')
    else:
        form = SolicitarFeriasForm()

    return render(request, 'funcionarios/colaborador/ferias.html', {
        'form': form,
        'funcionario': funcionario,
        'solicitacoes': solicitacoes,
        'historico': historico,
        'solicitacao_ativa': solicitacao_ativa,
        'tem_ativa': tem_ativa,
        'aviso_pendente': aviso_pendente,
        'saldo_dias': funcionario.dias_pendentes_ferias() or 0,
        'hoje': hoje_ferias,
    })


@login_required
def aceitar_sugestao_ferias_view(request, solicitacao_id):
    solicitacao = get_object_or_404(SolicitacaoFerias, id=solicitacao_id)

    if solicitacao.funcionario != request.user.usuario.funcionario:
        messages.error(request, 'Você não tem permissão para essa ação.')
        return redirect('solicitar_ferias')

    if solicitacao.status == 'sugestao_gestor':
        solicitacao.data_solicitada = solicitacao.data_sugerida
        solicitacao.dias_solicitados = solicitacao.dias_sugeridos
        solicitacao.data_sugerida = None
        solicitacao.dias_sugeridos = None
        if solicitacao.funcionario.eh_gestor or solicitacao.aviso_origem_id:
            solicitacao.status = 'pendente_rh'
            solicitacao.save()
            messages.success(request, 'Nova data aceita e enviada diretamente ao RH.')
        else:
            solicitacao.status = 'pendente_gestor'
            solicitacao.save()
            messages.success(request, 'Nova data aceita e enviada para análise do gestor.')

    return redirect('solicitar_ferias')


@login_required
def propor_outra_data_ferias_view(request, solicitacao_id):
    solicitacao = get_object_or_404(SolicitacaoFerias, id=solicitacao_id)

    if solicitacao.funcionario != request.user.usuario.funcionario:
        messages.error(request, 'Você não tem permissão para essa ação.')
        return redirect('solicitar_ferias')

    if request.method == 'POST':
        form = ProporOutraDataForm(request.POST)
        if form.is_valid():
            solicitacao.data_solicitada = form.cleaned_data['data_solicitada']
            solicitacao.dias_solicitados = form.cleaned_data['dias_solicitados']
            solicitacao.data_sugerida = None
            solicitacao.dias_sugeridos = None
            if solicitacao.funcionario.eh_gestor or solicitacao.aviso_origem_id:
                solicitacao.status = 'pendente_rh'
                solicitacao.save()
                messages.success(request, 'Nova data proposta e enviada diretamente ao RH.')
            else:
                solicitacao.status = 'pendente_gestor'
                solicitacao.save()
                messages.success(request, 'Nova data proposta e enviada para análise do gestor.')
            return redirect('solicitar_ferias')
    else:
        form = ProporOutraDataForm()

    return render(request, 'funcionarios/colaborador/propor_outra_data.html', {
        'form': form,
        'solicitacao': solicitacao,
    })


@login_required
def adiar_ferias_view(request, solicitacao_id):
    solicitacao = get_object_or_404(SolicitacaoFerias, id=solicitacao_id)

    if solicitacao.funcionario != request.user.usuario.funcionario:
        messages.error(request, 'Você não tem permissão para essa ação.')
        return redirect('solicitar_ferias')

    if solicitacao.status == 'sugestao_gestor':
        solicitacao.status = 'adiada'
        solicitacao.save()
        messages.success(request, 'Solicitação adiada. Você pode solicitar novamente quando quiser.')

    return redirect('solicitar_ferias')


# ---------- Férias — Gestor ----------

@login_required
@papel_required('gestor', 'rh')
def gestor_listar_ferias_view(request):
    usuario = request.user.usuario

    solicitacoes = SolicitacaoFerias.objects.filter(status='pendente_gestor').exclude(funcionario=usuario.funcionario)
    if usuario.papel == 'gestor' and not usuario.acesso_rh:
        solicitacoes = solicitacoes.filter(funcionario__setor=usuario.funcionario.setor)

    solicitacoes = solicitacoes.order_by('criado_em')

    return render(request, 'funcionarios/rh/gestor_listar_ferias.html', {
        'solicitacoes': solicitacoes,
    })


@login_required
@papel_required('gestor', 'rh')
def gestor_aprovar_ferias_view(request, solicitacao_id):
    usuario = request.user.usuario
    solicitacao = get_object_or_404(SolicitacaoFerias, id=solicitacao_id)

    if solicitacao.funcionario == usuario.funcionario:
        messages.error(request, 'Você não pode aprovar sua própria solicitação de férias. Ela é enviada direto ao RH.')
        return redirect('gestor_listar_ferias')

    if usuario.papel == 'gestor' and not usuario.acesso_rh and solicitacao.funcionario.setor != usuario.funcionario.setor:
        messages.error(request, 'Você não tem permissão para essa ação.')
        return redirect('gestor_listar_ferias')

    if solicitacao.status == 'pendente_gestor':
        solicitacao.status = 'pendente_rh'
        solicitacao.analisado_por_gestor = request.user
        solicitacao.save()
        messages.success(request, 'Solicitação aprovada e enviada ao RH.')

    return redirect('gestor_listar_ferias')


@login_required
@papel_required('gestor', 'rh')
def gestor_sugerir_ferias_view(request, solicitacao_id):
    usuario = request.user.usuario
    solicitacao = get_object_or_404(SolicitacaoFerias, id=solicitacao_id)

    if solicitacao.funcionario == usuario.funcionario:
        messages.error(request, 'Você não pode sugerir alteração na sua própria solicitação de férias. Ela é enviada direto ao RH.')
        return redirect('gestor_listar_ferias')

    if usuario.papel == 'gestor' and not usuario.acesso_rh and solicitacao.funcionario.setor != usuario.funcionario.setor:
        messages.error(request, 'Você não tem permissão para essa ação.')
        return redirect('gestor_listar_ferias')

    if request.method == 'POST':
        form = SugerirAlteracaoForm(request.POST)
        if form.is_valid() and solicitacao.status == 'pendente_gestor':
            solicitacao.data_sugerida = form.cleaned_data['data_sugerida']
            solicitacao.dias_sugeridos = form.cleaned_data['dias_sugeridos']
            solicitacao.observacao_gestor = form.cleaned_data['observacao_gestor']
            solicitacao.status = 'sugestao_gestor'
            solicitacao.analisado_por_gestor = request.user
            solicitacao.save()
            messages.success(request, 'Sugestão enviada ao colaborador.')
            return redirect('gestor_listar_ferias')
    else:
        form = SugerirAlteracaoForm()

    return render(request, 'funcionarios/rh/gestor_sugerir_ferias.html', {
        'form': form,
        'solicitacao': solicitacao,
    })


# ---------- Férias — Concluir (RH) e Avisos (gestor) ----------

@login_required
@papel_required('gestor', 'rh')
def concluir_ferias_view(request):
    usuario = request.user.usuario

    avisos = AvisoFerias.objects.all()
    if usuario.papel == 'gestor':
        avisos = avisos.filter(funcionario__setor__gestor=usuario.funcionario)
    avisos = avisos.order_by('visualizado', '-criado_em')

    pode_concluir = usuario.papel == 'rh' or usuario.acesso_rh
    solicitacoes = (
        SolicitacaoFerias.objects.filter(status='pendente_rh').order_by('criado_em')
        if pode_concluir else SolicitacaoFerias.objects.none()
    )

    confirmadas_qs = SolicitacaoFerias.objects.filter(
        status='aprovada'
    ).select_related('funcionario')
    if usuario.papel == 'gestor':
        confirmadas_qs = confirmadas_qs.filter(funcionario__setor__gestor=usuario.funcionario)
    hoje = timezone.now().date()
    ferias_confirmadas = sorted(
        (s for s in confirmadas_qs if s.data_fim >= hoje),
        key=lambda s: s.data_solicitada
    )

    return render(request, 'funcionarios/rh/concluir_ferias.html', {
        'avisos': avisos,
        'solicitacoes': solicitacoes,
        'pode_concluir': pode_concluir,
        'ferias_confirmadas': ferias_confirmadas,
    })


@login_required
@papel_required('rh')
def rh_concluir_ferias_view(request, solicitacao_id):
    solicitacao = get_object_or_404(SolicitacaoFerias, id=solicitacao_id)

    if solicitacao.status == 'pendente_rh':
        solicitacao.status = 'aprovada'
        solicitacao.concluido_por_rh = request.user
        solicitacao.save()
        messages.success(request, 'Férias aprovadas e concluídas.')

    return redirect('concluir_ferias')


# ---------- Vencimento de Férias e Avisos ----------

@login_required
@papel_required('rh')
def rh_vencimento_ferias_view(request):
    funcionarios = Funcionario.objects.filter(status='ativo', data_admissao__isnull=False)

    proximos_vencimento = []
    for funcionario in funcionarios:
        dias = funcionario.dias_para_vencer_ferias()
        if dias is not None and dias <= LIMITE_DIAS_ALERTA_FERIAS:
            proximos_vencimento.append({
                'funcionario': funcionario,
                'dias': dias,
                'dias_abs': abs(dias),
                'dias_pendentes': funcionario.dias_pendentes_ferias(),
                'vencida': funcionario.ferias_vencidas(),
                'vencimento': funcionario.proxima_data_vencimento(),
            })

    proximos_vencimento.sort(key=lambda item: item['dias'])

    return render(request, 'funcionarios/rh/vencimento_ferias.html', {
        'proximos_vencimento': proximos_vencimento,
    })


@login_required
@papel_required('rh')
def criar_aviso_ferias_view(request, funcionario_id):
    funcionario = get_object_or_404(Funcionario, id=funcionario_id)

    if funcionario.avisos_ferias.filter(solicitacoes_geradas__isnull=True).exists():
        messages.error(request, 'Já existe um aviso em aberto para este funcionário.')
        return redirect('rh_vencimento_ferias')

    if request.method == 'POST':
        form = AvisoFeriasForm(request.POST)
        if form.is_valid():
            AvisoFerias.objects.create(
                funcionario=funcionario,
                data_prevista=form.cleaned_data['data_prevista'],
                mensagem=form.cleaned_data['mensagem'],
                criado_por=request.user,
            )
            messages.success(request, 'Aviso enviado ao gestor.')
            return redirect('rh_vencimento_ferias')
    else:
        form = AvisoFeriasForm()

    return render(request, 'funcionarios/rh/criar_aviso_ferias.html', {
        'form': form,
        'funcionario': funcionario,
    })


@login_required
@papel_required('gestor', 'rh')
def marcar_aviso_visualizado_view(request, aviso_id):
    usuario = request.user.usuario
    aviso = get_object_or_404(AvisoFerias, id=aviso_id)

    if usuario.papel == 'gestor' and aviso.funcionario.setor.gestor != usuario.funcionario:
        messages.error(request, 'Você não tem permissão para essa ação.')
        return redirect('concluir_ferias')

    if not aviso.visualizado:
        aviso.visualizado = True
        aviso.visualizado_em = timezone.now()
        aviso.save()

    return redirect('concluir_ferias')