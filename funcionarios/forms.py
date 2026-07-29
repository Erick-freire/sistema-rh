from django import forms
from .models import Funcionario


class MeuPerfilForm(forms.ModelForm):
    class Meta:
        model = Funcionario
        fields = ['cpf', 'telefone', 'endereco', 'foto']