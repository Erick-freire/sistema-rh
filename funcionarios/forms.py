from django import forms
from .models import Funcionario, Documento


class MeuPerfilForm(forms.ModelForm):
    class Meta:
        model = Funcionario
        fields = ['cpf', 'telefone', 'endereco', 'foto']

class EnviarDocumentoForm(forms.ModelForm):
    class Meta:
        model = Documento
        fields = ['tipo_documento', 'arquivo']