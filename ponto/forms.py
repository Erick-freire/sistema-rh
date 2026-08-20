from django import forms
from django.forms import inlineformset_factory

from .models import AjustePonto, Equipamento, Feriado, FuncionarioJornada, Horario, HorarioDia


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
        fields = ['numero', 'nome', 'eh_12x36', 'considerar_feriados', 'separar_normais_noturnas']


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


class HorarioSelect(forms.Select):
    """
    Marca no HTML (`data-doze36`) quais opções são horário 12x36, pra tela
    trocar o rótulo/ajuda da data pra algo específico de ciclo (ver
    static/js/ui.js) sem precisar duplicar horário ou expor par/ímpar.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ids_12x36 = set(Horario.objects.filter(eh_12x36=True).values_list('id', flat=True))

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        valor_bruto = getattr(value, 'value', value)  # Django >=4.1 embrulha em ModelChoiceIteratorValue
        try:
            horario_id = int(valor_bruto)
        except (TypeError, ValueError):
            horario_id = None
        if horario_id in self._ids_12x36:
            option['attrs']['data-doze36'] = '1'
        return option


class FuncionarioJornadaForm(forms.ModelForm):
    class Meta:
        model = FuncionarioJornada
        fields = ['data_inicio', 'horario']
        widgets = {
            'data_inicio': forms.DateInput(attrs={'type': 'date'}),
            'horario': HorarioSelect,
        }


class JornadaOpcionalForm(forms.Form):
    """
    Igual ao FuncionarioJornadaForm, mas sem exigir horário nem data de início
    -- usado no pré-cadastro do funcionário, onde ainda não se sabe a jornada
    dele (a data de admissão do próprio pré-cadastro é usada como data_inicio
    quando um horário é escolhido).
    """
    horario = forms.ModelChoiceField(queryset=Horario.objects.all(), required=False, label='Horário', widget=HorarioSelect)


class AjustarMarcacaoForm(forms.ModelForm):
    class Meta:
        model = AjustePonto
        fields = ['tipo_evento', 'horario_ajustado', 'motivo']
        widgets = {'horario_ajustado': forms.TimeInput(attrs={'type': 'time'})}


class FeriadoForm(forms.ModelForm):
    class Meta:
        model = Feriado
        fields = ['data', 'descricao']
        widgets = {'data': forms.DateInput(attrs={'type': 'date'})}
