from django import forms

from funcionarios.models import Setor

from .models import RegraPremio, RegraPremioPrograma, RegraPremioSafra
from .services import funcionarios_da_folha


class CampoMes(forms.DateField):
    """
    Data com calendário (`type="date"`), mas o que vale é o mês: guarda o dia 1.
    Não é `type="month"` porque no Firefox e no Safari ele vira uma caixa de
    texto sem calendário.
    """
    widget = forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')
    input_formats = ['%Y-%m-%d']

    def to_python(self, value):
        data = super().to_python(value)
        return data.replace(day=1) if data else data


def _campo_mes(rotulo):
    return CampoMes(label=rotulo, required=False)


class RegraPremioForm(forms.ModelForm):
    """Regra de prêmio incentivo (o único tipo que sobrou no `RegraPremio`)."""
    ALVO_CHOICES = [
        ('setor_cargo', 'Um cargo do setor'),
        ('setor', 'Todo o setor'),
        ('pessoa', 'Uma pessoa'),
    ]
    alvo = forms.ChoiceField(label='Vale para', choices=ALVO_CHOICES, widget=forms.RadioSelect, initial='setor_cargo')
    vigente_de = _campo_mes('Vale a partir de')
    vigente_ate = _campo_mes('Vale até')

    class Meta:
        model = RegraPremio
        fields = ['alvo', 'setor', 'cargo', 'funcionario', 'valor', 'vigente_de', 'vigente_ate']
        labels = {'funcionario': 'Pessoa', 'valor': 'Valor (R$)'}
        widgets = {
            # Texto livre com sugestões: o <datalist> vem do template, a partir dos cargos dos ativos.
            'cargo': forms.TextInput(attrs={'list': 'cargos-existentes', 'autocomplete': 'off'}),
            'valor': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.instance.tipo = 'premio_incentivo'  # antes de validar: a checagem de regra repetida usa o tipo
        self.fields['setor'].queryset = Setor.objects.order_by('nome')
        self.fields['funcionario'].queryset = funcionarios_da_folha()
        self.fields['funcionario'].label_from_instance = lambda f: f'{f.nome} ({f.matricula or "—"})'
        if self.instance.pk:
            regra = self.instance
            self.initial['alvo'] = 'pessoa' if regra.funcionario_id else ('setor_cargo' if regra.cargo else 'setor')

    def clean(self):
        dados = super().clean()
        alvo = dados.get('alvo')
        # Só o que vale para o alvo escolhido fica na regra: o resto do formulário é ignorado.
        if alvo == 'pessoa':
            dados['setor'], dados['cargo'] = None, ''
            if not dados.get('funcionario'):
                self.add_error('funcionario', 'Escolha a pessoa.')
        else:
            dados['funcionario'] = None
            if not dados.get('setor'):
                self.add_error('setor', 'Escolha o setor.')
            if alvo == 'setor':
                dados['cargo'] = ''
            elif not (dados.get('cargo') or '').strip():
                self.add_error('cargo', 'Informe o cargo.')
            else:
                dados['cargo'] = ' '.join(dados['cargo'].split())
        return dados


class RegraProgramaForm(forms.ModelForm):
    """Cargo e valor da regra de prêmio programa; as pessoas são colocadas na tela da regra."""

    class Meta:
        model = RegraPremioPrograma
        fields = ['cargo', 'valor']
        labels = {'valor': 'Valor (R$)'}
        widgets = {
            'cargo': forms.TextInput(attrs={'list': 'cargos-existentes', 'autocomplete': 'off'}),
            'valor': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01'}),
        }


class RegraSafraForm(forms.ModelForm):
    """Nome, percentual e meses da safra; as pessoas são colocadas na tela da regra."""
    vigente_de = _campo_mes('Primeiro mês da safra')
    vigente_ate = _campo_mes('Último mês da safra')

    class Meta:
        model = RegraPremioSafra
        fields = ['nome', 'percentual', 'vigente_de', 'vigente_ate']
        labels = {'percentual': '% do salário'}
        widgets = {
            'nome': forms.TextInput(attrs={'placeholder': 'Ex.: Safra 2026'}),
            'percentual': forms.NumberInput(attrs={'step': '0.01', 'min': '0.01', 'max': '100'}),
        }
