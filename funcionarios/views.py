from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count
from .models import Usuario, Funcionario, Documento
import secrets
from django.contrib.auth.models import User
from .forms import MeuPerfilForm, EnviarDocumentoForm, FuncionarioForm, PreCadastroFuncionarioForm
from django.contrib.auth.forms import SetPasswordForm
from django.contrib.auth import update_session_auth_hash
from django.db.models import Q
from django.core.paginator import Paginator

def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect('dashboard')
        else:
            return render(request, 'funcionarios/login.html', {
                'erro': 'Usuário ou senha inválidos.'
            })

    return render(request, 'funcionarios/login.html')


def logout_view(request):
    logout(request)
    return redirect('login')


@login_required
def dashboard_view(request):
    try:
        usuario = request.user.usuario
    except Usuario.DoesNotExist:
        usuario = None

    if usuario is None:
        return render(request, 'funcionarios/sem_perfil.html')

    if usuario.papel == 'colaborador':
        return render(request, 'funcionarios/colaborador/dashboard.html')

    return dashboard_rh_view(request)

@login_required
def meu_perfil_view(request):
    try:
        funcionario = request.user.usuario.funcionario
    except Usuario.DoesNotExist:
        return render(request, 'funcionarios/sem_perfil.html')

    if request.method == 'POST':
        form = MeuPerfilForm(request.POST, request.FILES, instance=funcionario)
        if form.is_valid():
            form.save()
            messages.success(request, 'Perfil atualizado com sucesso.')
            return redirect('meu_perfil')
    else:
        form = MeuPerfilForm(instance=funcionario)

    return render(request, 'funcionarios/meu_perfil.html', {
        'form': form,
        'funcionario': funcionario,
    })
    
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

    return render(request, 'funcionarios/meus_documentos.html', {
        'documentos': documentos,
    })
    
from .decorators import papel_required


@login_required
@papel_required('rh', 'gestor')
def listar_funcionarios_view(request):
    usuario = request.user.usuario
    status_filtro = request.GET.get('status', 'ativo')
    busca = request.GET.get('busca', '').strip()

    if usuario.papel == 'rh':
        funcionarios = Funcionario.objects.filter(status=status_filtro)
    else:
        funcionarios = Funcionario.objects.filter(status=status_filtro, setor=usuario.funcionario.setor)

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
    })
    
@login_required
@papel_required('rh', 'gestor')
def cadastrar_funcionario_view(request):
    if request.method == 'POST':
        form = FuncionarioForm(request.POST, request.FILES)
        if form.is_valid():
            form.save()
            messages.success(request, 'Funcionário cadastrado com sucesso.')
            return redirect('listar_funcionarios')
    else:
        form = FuncionarioForm()

    return render(request, 'funcionarios/rh/cadastrar_funcionario.html', {
        'form': form,
    })
    
@login_required
@papel_required('rh', 'gestor')
def detalhe_funcionario_view(request, funcionario_id):
    usuario = request.user.usuario
    funcionario = get_object_or_404(Funcionario, id=funcionario_id)

    if usuario.papel == 'gestor' and funcionario.setor != usuario.funcionario.setor:
        messages.error(request, 'Você não tem permissão para ver este funcionário.')
        return redirect('listar_funcionarios')

    documentos = funcionario.documentos.all().order_by('-data_upload')

    return render(request, 'funcionarios/rh/detalhe_funcionario.html', {
        'funcionario': funcionario,
        'documentos': documentos,
    })

@login_required
@papel_required('rh', 'gestor')
def dashboard_rh_view(request):
    usuario = request.user.usuario

    if usuario.papel == 'rh':
        funcionarios = Funcionario.objects.filter(status='ativo')
    else:
        funcionarios = Funcionario.objects.filter(status='ativo', setor=usuario.funcionario.setor)

    total_funcionarios = funcionarios.count()
    funcionarios_por_setor = funcionarios.values('setor__nome').annotate(total=Count('id'))
    documentos_recentes = Documento.objects.filter(
        funcionario__in=funcionarios
    ).order_by('-data_upload')[:5]

    return render(request, 'funcionarios/rh/dashboard.html', {
        'total_funcionarios': total_funcionarios,
        'funcionarios_por_setor': funcionarios_por_setor,
        'documentos_recentes': documentos_recentes,
    })
    
@login_required
@papel_required('rh', 'gestor')
def cadastrar_funcionario_view(request):
    if request.method == 'POST':
        form = PreCadastroFuncionarioForm(request.POST)
        if form.is_valid():
            funcionario = form.save(commit=False)
            funcionario.status = 'pendente'
            funcionario.save()

            senha_gerada = secrets.token_urlsafe(9)

            user = User.objects.create_user(
                username=funcionario.matricula,
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

    return render(request, 'funcionarios/rh/cadastrar_funcionario.html', {
        'form': form,
    })
    
@login_required
def trocar_senha_view(request):
    try:
        usuario = request.user.usuario
    except Usuario.DoesNotExist:
        return redirect('dashboard')

    if not usuario.senha_provisoria:
        return redirect('dashboard')

    if request.method == 'POST':
        form = SetPasswordForm(request.user, request.POST)
        if form.is_valid():
            form.save()
            usuario.senha_provisoria = False
            usuario.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, 'Senha alterada com sucesso.')
            return redirect('dashboard')
    else:
        form = SetPasswordForm(request.user)

    return render(request, 'funcionarios/trocar_senha.html', {
        'form': form,
    })