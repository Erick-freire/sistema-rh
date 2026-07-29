from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import Usuario, Funcionario, Documento
from .forms import MeuPerfilForm, EnviarDocumentoForm

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

    return render(request, 'funcionarios/rh/dashboard.html')

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
            messages.success(request, 'Documento enviado com sucesso.')
            return redirect('enviar_documento')
    else:
        form = EnviarDocumentoForm()

    return render(request, 'funcionarios/enviar_documento.html', {
        'form': form,
    })