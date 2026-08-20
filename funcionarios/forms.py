from django import forms
from .models import Funcionario, Documento, SolicitacaoFerias, AvisoFerias


class MeuPerfilForm(forms.ModelForm):
    CAMPOS_OBRIGATORIOS = [c for c in Funcionario.CAMPOS_PESSOAIS_OBRIGATORIOS if c != 'cpf']

    class Meta:
        model = Funcionario
        fields = [
            'telefone', 'endereco',
            'sexo', 'estado_civil', 'tem_filho_menor_idade',
            'email', 'banco', 'agencia', 'numero_conta',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.CAMPOS_OBRIGATORIOS:
            self.fields[campo].required = True

class FotoPerfilForm(forms.ModelForm):
    class Meta:
        model = Funcionario
        fields = ['foto']

class EnviarDocumentoForm(forms.ModelForm):
    class Meta:
        model = Documento
        fields = ['tipo_documento', 'arquivo']

class DadosCadastraisForm(forms.ModelForm):
    data_admissao = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
    )

    class Meta:
        model = Funcionario
        fields = ['matricula', 'setor', 'data_admissao']

class FuncionarioForm(forms.ModelForm):
    class Meta:
        model = Funcionario
        fields = [
            'nome', 'matricula', 'cargo', 'setor', 'status', 'cpf', 'telefone', 'endereco', 'foto',
            'sexo', 'estado_civil', 'tem_filho_menor_idade',
            'email', 'banco', 'agencia', 'numero_conta',
        ]

class PreCadastroFuncionarioForm(forms.ModelForm):
    class Meta:
        model = Funcionario
        fields = ['nome', 'cpf', 'cargo', 'setor', 'data_admissao']
        widgets = {
            'data_admissao': forms.DateInput(attrs={'type': 'date'}),
        }

    def clean_cpf(self):
        cpf = ''.join(filter(str.isdigit, self.cleaned_data['cpf']))
        if len(cpf) != 11:
            raise forms.ValidationError('CPF deve ter 11 dígitos.')
        if Funcionario.objects.filter(cpf=cpf).exists():
            raise forms.ValidationError('Já existe um funcionário cadastrado com este CPF.')
        return cpf

class SolicitarFeriasForm(forms.ModelForm):
    data_solicitada = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        label='Data de início desejada',
    )

    class Meta:
        model = SolicitacaoFerias
        fields = ['data_solicitada', 'dias_solicitados']


class ProporOutraDataForm(forms.Form):
    data_solicitada = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        label='Nova data desejada',
    )
    dias_solicitados = forms.IntegerField(min_value=1, label='Quantidade de dias')


class SugerirAlteracaoForm(forms.Form):
    data_sugerida = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        label='Data sugerida',
    )
    dias_sugeridos = forms.IntegerField(min_value=1, label='Dias sugeridos')
    observacao_gestor = forms.CharField(widget=forms.Textarea, required=False, label='Observação')
    
class AvisoFeriasForm(forms.Form):
    data_prevista = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        label='Data prevista para as férias',
    )
    mensagem = forms.CharField(widget=forms.Textarea, required=False, label='Mensagem (opcional)')