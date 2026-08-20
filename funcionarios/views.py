import secrets

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
from django.utils import timezone

from .decorators import papel_required
from .models import Usuario, Funcionario, Documento, TipoDocumento, SolicitacaoFerias, AvisoFerias
from .forms import (
    MeuPerfilForm, FotoPerfilForm, EnviarDocumentoForm, FuncionarioForm, PreCadastroFuncionarioForm,
    DadosCadastraisForm, SolicitarFeriasForm, ProporOutraDataForm, SugerirAlteracaoForm, AvisoFeriasForm
)

# Só pra mostrar/atribuir a jornada de ponto na ficha do funcionário -- a
# atribuição em si continua sendo feita pela view do app `ponto`.
from ponto.forms import FuncionarioJornadaForm, JornadaOpcionalForm
from ponto.models import FuncionarioJornada


LIMITE_DIAS_ALERTA_FERIAS = 60

LIMITE_TENTATIVAS_LOGIN_USUARIO = 5
LIMITE_TENTATIVAS_LOGIN_IP = 20
JANELA_BLOQUEIO_LOGIN = 15 * 60  # segundos

MENSAGEM_BLOQUEIO_LOGIN = 'Muitas tentativas de login. Tente novamente em alguns minutos.'
MENSAGEM_CREDENCIAL_INVALIDA = 'Usuário ou senha inválidos.'


# ---------- Autenticação ----------

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username', '')
        password = request.POST.get('password')
        ip = request.META.get('REMOTE_ADDR', 'desconhecido')

        chave_usuario = f'login_falhas:usuario:{username}'
        chave_ip = f'login_falhas:ip:{ip}'

        if cache.get(chave_usuario, 0) >= LIMITE_TENTATIVAS_LOGIN_USUARIO or \
           cache.get(chave_ip, 0) >= LIMITE_TENTATIVAS_LOGIN_IP:
            return render(request, 'funcionarios/login.html', {
                'erro': MENSAGEM_BLOQUEIO_LOGIN
            })

        user = authenticate(request, username=username, password=password)

        if user is not None:
            cache.delete(chave_usuario)
            login(request, user)
            return redirect('dashboard')
        else:
            cache.set(chave_usuario, cache.get(chave_usuario, 0) + 1, JANELA_BLOQUEIO_LOGIN)
            cache.set(chave_ip, cache.get(chave_ip, 0) + 1, JANELA_BLOQUEIO_LOGIN)
            return render(request, 'funcionarios/login.html', {
                'erro': MENSAGEM_CREDENCIAL_INVALIDA
            })

    return render(request, 'funcionarios/login.html')


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
        tipos_obrigatorios = funcionario.tipos_documentos_obrigatorios()
        tipos_enviados_ids = set(funcionario.documentos.values_list('tipo_documento_id', flat=True))
        tipos_pendentes = [t for t in tipos_obrigatorios if t.id not in tipos_enviados_ids]
        total_obrigatorios = len(tipos_obrigatorios)

        solicitacao_ferias = funcionario.solicitacoes_ferias.order_by('-criado_em').first()
        tem_ativa = funcionario.solicitacoes_ferias.exclude(status__in=['aprovada', 'adiada']).exists()
        aviso_pendente = funcionario.aviso_ferias_pendente_acao()

        return render(request, 'funcionarios/colaborador/dashboard.html', {
            'funcionario': funcionario,
            'tipos_pendentes': tipos_pendentes,
            'total_obrigatorios': total_obrigatorios,
            'total_enviados': total_obrigatorios - len(tipos_pendentes),
            'solicitacao_ferias': solicitacao_ferias,
            'hoje': timezone.now().date(),
            'tem_ativa': tem_ativa,
            'aviso_pendente': aviso_pendente,
        })

    return dashboard_rh_view(request)


@login_required
@papel_required('rh', 'gestor')
def dashboard_rh_view(request):
    usuario = request.user.usuario

    if usuario.papel == 'rh' or usuario.acesso_rh:
        funcionarios_do_escopo = Funcionario.objects.all()
    else:
        funcionarios_do_escopo = Funcionario.objects.filter(setor=usuario.funcionario.setor)

    funcionarios_ativos = funcionarios_do_escopo.filter(status='ativo')
    total_funcionarios = funcionarios_ativos.count()
    cadastros_pendentes = funcionarios_do_escopo.filter(status='pendente').count()
    funcionarios_por_setor = funcionarios_ativos.values('setor__nome').annotate(total=Count('id')).order_by('-total')
    documentos_recentes = Documento.objects.filter(
        funcionario__in=funcionarios_do_escopo
    ).order_by('-data_upload')[:5]

    ferias_aguardando_gestor = SolicitacaoFerias.objects.filter(
        status='pendente_gestor', funcionario__in=funcionarios_do_escopo
    ).count()
    ferias_aguardando_rh = SolicitacaoFerias.objects.filter(
        status='pendente_rh', funcionario__in=funcionarios_do_escopo
    ).count()

    maior_total_setor = max(
        [item['total'] for item in funcionarios_por_setor], default=0
    )

    return render(request, 'funcionarios/rh/dashboard.html', {
        'total_funcionarios': total_funcionarios,
        'cadastros_pendentes': cadastros_pendentes,
        'ferias_aguardando_gestor': ferias_aguardando_gestor,
        'ferias_aguardando_rh': ferias_aguardando_rh,
        'funcionarios_por_setor': funcionarios_por_setor,
        'maior_total_setor': maior_total_setor,
        'documentos_recentes': documentos_recentes,
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
                    Documento.objects.create(
                        funcionario=funcionario,
                        tipo_documento=tipo,
                        arquivo=arquivo,
                        enviado_por=request.user,
                    )

            if funcionario.status == 'pendente' and funcionario.cadastro_completo():
                funcionario.status = 'ativo'
                funcionario.save()
                messages.success(request, 'Perfil atualizado. Seu cadastro foi concluído com sucesso!')
            else:
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

@login_required
def enviar_documento_view(request):
    try:
        funcionario = request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return render(request, 'funcionarios/sem_perfil.html')

    if request.method == 'POST':
        form = EnviarDocumentoForm(request.POST, request.FILES)
        if form.is_valid():
            documento = form.save(commit=False)
            documento.funcionario = funcionario
            documento.enviado_por = request.user
            documento.save()

            if funcionario.status == 'pendente' and funcionario.cadastro_completo():
                funcionario.status = 'ativo'
                funcionario.save()
                messages.success(request, 'Documento enviado. Seu cadastro foi concluído com sucesso!')
            else:
                messages.success(request, 'Documento enviado com sucesso.')

            return redirect('enviar_documento')
    else:
        form = EnviarDocumentoForm()

    return render(request, 'funcionarios/enviar_documento.html', {
        'form': form,
    })


@login_required
def meus_documentos_view(request):
    try:
        funcionario = request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return render(request, 'funcionarios/sem_perfil.html')

    documentos = funcionario.documentos.all().order_by('-data_upload')

    tipos_obrigatorios = funcionario.tipos_documentos_obrigatorios()
    tipos_enviados_ids = set(documentos.values_list('tipo_documento_id', flat=True))
    tipos_pendentes = [t for t in tipos_obrigatorios if t.id not in tipos_enviados_ids]

    return render(request, 'funcionarios/meus_documentos.html', {
        'documentos': documentos,
        'tipos_pendentes': tipos_pendentes,
    })


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
    contagem_pendentes = base_qs.filter(status='pendente').count()
    contagem_inativos = base_qs.filter(status='inativo').count()

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
            funcionario.status = 'pendente'
            funcionario.save()

            horario = jornada_form.cleaned_data.get('horario')
            if horario:
                jornada = FuncionarioJornada(
                    funcionario=funcionario,
                    data_inicio=funcionario.data_admissao or timezone.now().date(),
                    horario=horario,
                    paridade_ciclo=jornada_form.cleaned_data.get('paridade_ciclo') or None,
                )
                jornada.resolver_paridade()
                jornada.save()

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

    documentos = funcionario.documentos.all().order_by('-data_upload')

    tipos_obrigatorios = funcionario.tipos_documentos_obrigatorios()
    tipos_enviados_ids = set(documentos.values_list('tipo_documento_id', flat=True))
    checklist_documentos = [
        {'tipo': tipo, 'enviado': tipo.id in tipos_enviados_ids}
        for tipo in tipos_obrigatorios
    ]
    total_obrigatorios = len(tipos_obrigatorios)
    total_enviados_obrigatorios = sum(1 for item in checklist_documentos if item['enviado'])

    return render(request, 'funcionarios/rh/detalhe_funcionario.html', {
        'funcionario': funcionario,
        'documentos': documentos,
        'checklist_documentos': checklist_documentos,
        'total_obrigatorios': total_obrigatorios,
        'total_enviados_obrigatorios': total_enviados_obrigatorios,
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


# ---------- Férias — Colaborador ----------

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
        'solicitacao_ativa': solicitacao_ativa,
        'tem_ativa': tem_ativa,
        'aviso_pendente': aviso_pendente,
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