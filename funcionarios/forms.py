from django import forms
from .models import Funcionario, Documento, SolicitacaoFerias


class MeuPerfilForm(forms.ModelForm):
    class Meta:
        model = Funcionario
        fields = ['cpf', 'telefone', 'endereco', 'foto']

class EnviarDocumentoForm(forms.ModelForm):
    class Meta:
        model = Documento
        fields = ['tipo_documento', 'arquivo']

class FuncionarioForm(forms.ModelForm):
    class Meta:
        model = Funcionario
        fields = ['nome', 'matricula', 'cargo', 'setor', 'status', 'cpf', 'telefone', 'endereco', 'foto']

class PreCadastroFuncionarioForm(forms.ModelForm):
    class Meta:
        model = Funcionario
        fields = ['nome', 'cpf', 'matricula', 'cargo', 'setor']
        
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