from datetime import date, timedelta

from django import forms
from django.db.models import Count
from django.forms import inlineformset_factory
from django.utils import timezone

from funcionarios.models import Funcionario, Setor

from .espelho_pdf import funcionarios_do_setor, funcionarios_que_batem_ponto
from .models import AjustePonto, Equipamento, Feriado, FuncionarioJornada, Horario, HorarioDia, MarcacaoRelogio
from .turnos import DURACAO_MAXIMA_TURNO, duracao_do_turno
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

_CAMPOS_HORARIO_DIA = ('entrada1', 'saida1', 'entrada2', 'saida2', 'entrada3', 'saida3')


class HorarioDiaForm(forms.ModelForm):
    def clean(self):
        cleaned_data = super().clean()
        horas = [cleaned_data.get(campo) for campo in _CAMPOS_HORARIO_DIA if cleaned_data.get(campo)]
        duracao = duracao_do_turno(horas)
        # Um par com saída antes da entrada é lido como turno que atravessa a
        # meia-noite -- certo no noturno (22:00-00:00), errado num 15:20-15:00.
        if duracao > DURACAO_MAXIMA_TURNO:
            minutos = int(duracao.total_seconds() // 60)
            raise forms.ValidationError(
                f'Da primeira entrada à última saída dá {minutos // 60}h{minutos % 60:02d}. '
                'Confira se alguma saída ficou antes da entrada (ex.: 15:20 às 15:00).'
            )
        return cleaned_data


HorarioDiaFormSet = inlineformset_factory(
    Horario, HorarioDia,
    form=HorarioDiaForm,
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

    A consulta ao banco fica em `create_option` (só roda quando o widget é
    de fato renderizado numa request) -- nunca em `__init__`, porque
    `Meta.widgets`/`widget=` instancia o widget na hora em que o módulo é
    importado, e um `__init__` que consulta o banco travaria qualquer
    comando (`migrate`, `showmigrations`...) se o banco estiver fora do ar
    nesse instante.
    """
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        valor_bruto = getattr(value, 'value', value)  # Django >=4.1 embrulha em ModelChoiceIteratorValue
        try:
            horario_id = int(valor_bruto)
        except (TypeError, ValueError):
            horario_id = None
        if horario_id is not None and Horario.objects.filter(id=horario_id, eh_12x36=True).exists():
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
    """
    Lançamento do RH. A data é a data REAL da batida, livre: vem preenchida pela
    view (dia da batida original na correção, dia da linha do espelho na batida
    que faltou) e o RH troca quando precisa -- ex.: saída depois da meia-noite.
    """
    class Meta:
        model = AjustePonto
        fields = ['data', 'tipo_evento', 'horario_ajustado', 'motivo']
        widgets = {
            'data': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'horario_ajustado': forms.TimeInput(attrs={'type': 'time'}),
        }
        help_texts = {
            'data': 'Data em que a batida realmente aconteceu. Para uma saída depois da meia-noite, use o dia seguinte.',
        }


class MarcacaoOriginalChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        # Com data: a lista tem batidas de três dias (ver SolicitarAjusteForm).
        return f'{timezone.localtime(obj.momento):%d/%m %H:%M} ({obj.get_tipo_evento_display()})'


class SolicitarAjusteForm(forms.ModelForm):
    """
    Pedido de correção feito pelo próprio colaborador -- vira um AjustePonto
    com status='pendente', só passa a valer pro cálculo depois que o RH
    aprovar (ver `ajustes_pendentes_view`/`aprovar_ajuste_view`).

    `marcacao_original` só oferece as batidas do próprio funcionário do dia
    anterior ao dia seguinte à data informada (nunca um dropdown livre) --
    restrição de segurança (não vaza batida de outro funcionário) e de
    corretude: sem isso, corrigir uma batida existente sem referenciá-la faria
    o motor de cálculo contar as duas (a errada e a corrigida) em vez de uma
    substituir a outra. Os dias vizinhos entram por causa do turno noturno: a
    saída das 06:03 é do dia seguinte ao turno, e uma volta às 23:58 corrigida
    para 00:05 tem a data (real) do dia seguinte ao da batida original.
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
        help_texts = {
            'data': 'Informe a data em que a batida realmente aconteceu. Para uma saída depois da meia-noite, use o dia seguinte.',
        }

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
            inicio = intervalo_do_dia(data_inicial - timedelta(days=1))[0]
            fim = intervalo_do_dia(data_inicial + timedelta(days=1))[1]
            self.fields['marcacao_original'].queryset = MarcacaoRelogio.objects.filter(
                funcionario=funcionario, momento__gte=inicio, momento__lt=fim,
            ).order_by('momento')

    def clean(self):
        cleaned_data = super().clean()
        data = cleaned_data.get('data')
        tipo_evento = cleaned_data.get('tipo_evento')

        # Não precisa checar aqui se `marcacao_original` é perto de `data`: a
        # queryset do campo já é escopada pelo `data` submetido (ver __init__),
        # então uma batida mais longe já cai como "opção inválida" antes de
        # chegar aqui.
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


# Setor grande em período longo passaria do tempo que o proxy de produção
# espera por uma resposta (~60 s). Medido em out/2026: 40 pessoas x 30 dias
# = 20 s (cálculo + PDF); 2500 pessoas-dia fica perto de 40 s.
LIMITE_PESSOAS_DIA_ESPELHO = 2500


class ExportarEspelhoForm(forms.Form):
    """
    Filtro da tela Espelho e cálculo: um funcionário (espelho na tela ou em
    PDF) ou o setor inteiro (só em PDF, uma página por pessoa). Os nomes dos
    campos são os mesmos parâmetros da URL da tela.
    """
    FILTRO_CHOICES = [('funcionario', 'Um funcionário'), ('setor', 'Setor inteiro (PDF)')]

    data_inicio = forms.DateField(label='De', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    data_fim = forms.DateField(label='Até', widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    filtro = forms.ChoiceField(label='Espelho de', choices=FILTRO_CHOICES, widget=forms.RadioSelect, initial='funcionario')
    setor = forms.ModelChoiceField(label='Setor', queryset=Setor.objects.order_by('nome'), required=False)
    # Todos, inclusive inativos: o espelho de quem saiu ainda é pedido (ex.: na rescisão).
    funcionario = forms.ModelChoiceField(
        label='Funcionário', queryset=Funcionario.objects.order_by('nome'), required=False,
    )

    def __init__(self, *args, usuario=None, **kwargs):
        # A tela também tem o formulário de jornada, com outro campo `data_inicio`: ids próprios evitam repetir.
        kwargs.setdefault('auto_id', 'espelho_%s')
        super().__init__(*args, **kwargs)
        # O gestor só escolhe (e só exporta) a equipe e os setores dele.
        if usuario is not None and not usuario.eh_dp_ou_rh():
            self.fields['funcionario'].queryset = usuario.equipe().order_by('nome')
            self.fields['setor'].queryset = usuario.setores_da_equipe().order_by('nome')
        # Quantos entram no espelho de cada setor, ao lado do nome.
        por_setor = dict(
            funcionarios_que_batem_ponto().order_by().values_list('setor').annotate(total=Count('id'))
        )
        self.fields['setor'].label_from_instance = lambda setor: f'{setor.nome} ({por_setor.get(setor.id, 0)})'

    def clean(self):
        dados = super().clean()
        inicio, fim = dados.get('data_inicio'), dados.get('data_fim')
        if inicio and fim and fim < inicio:
            self.add_error('data_fim', 'A data final não pode ser antes da inicial.')
            return dados

        if dados.get('filtro') == 'funcionario':
            if dados.get('funcionario') is None:
                self.add_error('funcionario', 'Escolha o funcionário.')
            else:
                dados['funcionarios'] = [dados['funcionario']]
            return dados

        setor = dados.get('setor')
        if setor is None:
            self.add_error('setor', 'Escolha o setor.')
            return dados
        pessoas = list(funcionarios_do_setor(setor))
        if not pessoas:
            self.add_error('setor', 'Nenhum funcionário ativo que bate ponto neste setor.')
        elif inicio and fim and len(pessoas) * ((fim - inicio).days + 1) > LIMITE_PESSOAS_DIA_ESPELHO:
            dias = LIMITE_PESSOAS_DIA_ESPELHO // len(pessoas)
            self.add_error(
                None,
                f'Período longo demais para o setor inteiro ({len(pessoas)} pessoas): para este setor, '
                f'escolha até {dias} dias, ou exporte por funcionário.',
            )
        dados['funcionarios'] = pessoas
        return dados
