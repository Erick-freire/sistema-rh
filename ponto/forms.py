from django import forms
from django.forms import inlineformset_factory

from .models import Equipamento, Escala, EscalaItem, Horario, HorarioDia


class EquipamentoForm(forms.ModelForm):
    senha_admin = forms.CharField(
        label='Senha de admin do equipamento',
        widget=forms.PasswordInput(render_value=False),
        required=False,
        help_text='Deixe em branco para manter a senha atual.',
    )

    class Meta:
        model = Equipamento
        fields = [
            'nome', 'ip', 'porta', 'usa_https', 'modelo', 'local', 'numero_serie',
            'ativo', 'usuario_admin', 'senha_admin',
        ]

    def clean_senha_admin(self):
        senha = self.cleaned_data.get('senha_admin')
        if not senha and not (self.instance and self.instance.pk):
            raise forms.ValidationError('Informe a senha de admin do equipamento.')
        return senha

    def save(self, commit=True):
        equipamento = super().save(commit=False)
        nova_senha = self.cleaned_data.get('senha_admin')
        if nova_senha:
            equipamento.senha_admin = nova_senha
        if commit:
            equipamento.save()
        return equipamento


class HorarioForm(forms.ModelForm):
    class Meta:
        model = Horario
        fields = ['numero', 'nome', 'considerar_feriados', 'separar_normais_noturnas']


_TIME_WIDGET = forms.TimeInput(attrs={'type': 'time'})

HorarioDiaFormSet = inlineformset_factory(
    Horario, HorarioDia,
    fields=['dia_semana', 'entrada1', 'saida1', 'entrada2', 'saida2', 'entrada3', 'saida3'],
    extra=7, max_num=7, can_delete=False,
    widgets={
        'dia_semana': forms.HiddenInput(),
        'entrada1': _TIME_WIDGET, 'saida1': _TIME_WIDGET,
        'entrada2': _TIME_WIDGET, 'saida2': _TIME_WIDGET,
        'entrada3': _TIME_WIDGET, 'saida3': _TIME_WIDGET,
    },
)

DIAS_SEMANA_INICIAIS = [{'dia_semana': dia} for dia in range(1, 8)]


class EscalaForm(forms.ModelForm):
    class Meta:
        model = Escala
        fields = ['numero', 'descricao', 'tamanho_ciclo', 'data_base']
        widgets = {'data_base': forms.DateInput(attrs={'type': 'date'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            # Mudar o tamanho do ciclo depois de criado deixaria as posições
            # dos itens já cadastrados inconsistentes -- exclui e recria em vez de editar.
            self.fields['tamanho_ciclo'].disabled = True


def escala_item_formset_factory(escala):
    return inlineformset_factory(
        Escala, EscalaItem,
        fields=['posicao', 'horario'],
        extra=0, max_num=escala.tamanho_ciclo, can_delete=False,
        widgets={'posicao': forms.HiddenInput()},
    )
