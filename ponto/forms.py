from datetime import date

from django import forms
from django.forms import inlineformset_factory
from django.utils import timezone

from .models import AjustePonto, Equipamento, Feriado, FuncionarioJornada, Horario, HorarioDia, MarcacaoRelogio
from .utils import intervalo_do_dia


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


class MarcacaoOriginalChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{timezone.localtime(obj.momento):%H:%M} ({obj.get_tipo_evento_display()})'


class SolicitarAjusteForm(forms.ModelForm):
    """
    Pedido de correção feito pelo próprio colaborador -- vira um AjustePonto
    com status='pendente', só passa a valer pro cálculo depois que o RH
    aprovar (ver `ajustes_pendentes_view`/`aprovar_ajuste_view`).

    `marcacao_original` só oferece as batidas do próprio funcionário no dia
    informado (nunca um dropdown livre) -- restrição de segurança (não vaza
    batida de outro funcionário) e de corretude: sem isso, corrigir uma
    batida existente sem referenciá-la faria o motor de cálculo contar as
    duas (a errada e a corrigida) em vez de uma substituir a outra.
    """
    class Meta:
        model = AjustePonto
        fields = ['data', 'tipo_evento', 'horario_ajustado', 'marcacao_original', 'motivo']
        field_classes = {'marcacao_original': MarcacaoOriginalChoiceField}
        widgets = {
            'data': forms.DateInput(attrs={'type': 'date'}),
            'horario_ajustado': forms.TimeInput(attrs={'type': 'time'}),
        }
        labels = {'marcacao_original': 'Corrigindo uma batida existente?'}

    def __init__(self, *args, funcionario, **kwargs):
        super().__init__(*args, **kwargs)
        self.funcionario = funcionario
        self.fields['marcacao_original'].empty_label = 'Nenhuma -- foi uma batida que faltou'
        self.fields['marcacao_original'].queryset = MarcacaoRelogio.objects.none()

        data_inicial = self.data.get('data') or self.initial.get('data')
        if isinstance(data_inicial, str):
            try:
                data_inicial = date.fromisoformat(data_inicial)
            except ValueError:
                data_inicial = None
        if data_inicial:
            # Range de datetime (não `momento__date=`) -- ver ponto/utils.py:
            # esse lookup depende de tabela de timezone do MySQL que pode não
            # estar carregada no servidor.
            inicio, fim = intervalo_do_dia(data_inicial)
            self.fields['marcacao_original'].queryset = MarcacaoRelogio.objects.filter(
                funcionario=funcionario, momento__gte=inicio, momento__lt=fim,
            ).order_by('momento')

    def clean(self):
        cleaned_data = super().clean()
        data = cleaned_data.get('data')
        tipo_evento = cleaned_data.get('tipo_evento')

        # Não precisa checar aqui se `marcacao_original` é do mesmo dia que
        # `data`: a queryset do campo já é escopada pelo `data` submetido (ver
        # __init__), então um valor de outro dia já cai como "opção inválida"
        # antes de chegar aqui.
        if data and tipo_evento and AjustePonto.objects.filter(
            funcionario=self.funcionario, data=data, tipo_evento=tipo_evento, status='pendente',
        ).exists():
            raise forms.ValidationError(
                'Já existe uma solicitação pendente pra esse dia e tipo de evento. '
                'Aguarde o RH decidir a primeira antes de enviar outra.'
            )

        return cleaned_data


class RejeitarAjusteForm(forms.Form):
    observacao_rh = forms.CharField(widget=forms.Textarea, required=True, label='Motivo da rejeição')


class FeriadoForm(forms.ModelForm):
    class Meta:
        model = Feriado
        fields = ['data', 'descricao']
        widgets = {'data': forms.DateInput(attrs={'type': 'date'})}
