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


def _validar_paridade(horario, paridade_ciclo):
    if horario and horario.eh_12x36:
        return None
    if paridade_ciclo:
        raise forms.ValidationError('Par/ímpar só se aplica a um horário 12x36.')
    return None


class FuncionarioJornadaForm(forms.ModelForm):
    class Meta:
        model = FuncionarioJornada
        fields = ['data_inicio', 'horario', 'paridade_ciclo']
        widgets = {'data_inicio': forms.DateInput(attrs={'type': 'date'})}

    def clean(self):
        cleaned_data = super().clean()
        _validar_paridade(cleaned_data.get('horario'), cleaned_data.get('paridade_ciclo'))
        return cleaned_data


class JornadaOpcionalForm(forms.Form):
    """
    Igual ao FuncionarioJornadaForm, mas sem exigir horário -- usado no
    pré-cadastro do funcionário, onde ainda não se sabe a jornada dele.
    """
    horario = forms.ModelChoiceField(queryset=Horario.objects.all(), required=False, label='Horário')
    paridade_ciclo = forms.ChoiceField(
        choices=[('', '(descobrir pela data de admissão)')] + FuncionarioJornada.PARIDADE_CHOICES,
        required=False, label='Par/ímpar (só 12x36)',
    )

    def clean(self):
        cleaned_data = super().clean()
        _validar_paridade(cleaned_data.get('horario'), cleaned_data.get('paridade_ciclo'))
        return cleaned_data


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
