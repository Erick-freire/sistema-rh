from django import forms

from .models import SolicitacaoAssinatura


class EnviarParaAssinaturaForm(forms.ModelForm):
    class Meta:
        model = SolicitacaoAssinatura
        fields = ['arquivo_original']
        labels = {'arquivo_original': 'Arquivo recebido para assinar'}
