from django import forms
from .models import Atestado, Funcionario, Documento, SolicitacaoFerias, AvisoFerias, TipoDocumento, TermoComodato, ItemComodato, ItemCatalogoComodato
from django.forms import BaseInlineFormSet, inlineformset_factory


class MeuPerfilForm(forms.ModelForm):
    CAMPOS_OBRIGATORIOS = [c for c in Funcionario.CAMPOS_PESSOAIS_OBRIGATORIOS if c != 'cpf']

    class Meta:
        model = Funcionario
        fields = [
            'telefone', 'endereco', 'pis',
            'sexo', 'estado_civil', 'tem_filho_menor_idade',
            'email', 'banco', 'agencia', 'numero_conta',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.CAMPOS_OBRIGATORIOS:
            self.fields[campo].required = True

    def clean_pis(self):
        pis = ''.join(filter(str.isdigit, self.cleaned_data.get('pis') or ''))
        if len(pis) != 11:
            raise forms.ValidationError('PIS deve ter 11 dígitos.')
        if Funcionario.objects.filter(pis=pis).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError('Já existe um funcionário cadastrado com este PIS.')
        return pis

class FotoPerfilForm(forms.ModelForm):
    class Meta:
        model = Funcionario
        fields = ['foto']

class EnviarDocumentoForm(forms.ModelForm):
    class Meta:
        model = Documento
        fields = ['tipo_documento', 'arquivo']

    def __init__(self, *args, funcionario=None, **kwargs):
        super().__init__(*args, **kwargs)
        if funcionario is not None:
            queryset = self.fields['tipo_documento'].queryset.exclude(nome='PIS')
            if funcionario.status not in ('aguardando_assinatura', 'em_validacao'):
                queryset = queryset.exclude(eh_contrato=True)
            ids_aplicaveis = [tipo.id for tipo in queryset if tipo.aplica_a(funcionario)]
            self.fields['tipo_documento'].queryset = queryset.filter(id__in=ids_aplicaveis)


class RejeitarDocumentoForm(forms.Form):
    motivo = forms.CharField(widget=forms.Textarea, required=True, label='Motivo da rejeição')

class DadosCadastraisForm(forms.ModelForm):
    data_admissao = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
    )

    class Meta:
        model = Funcionario
        fields = ['matricula', 'setor', 'data_admissao', 'salario']
        widgets = {
            'salario': forms.NumberInput(attrs={'step': '0.01', 'min': '0'}),
        }

class AtestadoForm(forms.ModelForm):
    class Meta:
        model = Atestado
        fields = ['data_inicio', 'data_fim', 'motivo']
        widgets = {
            'data_inicio': forms.DateInput(attrs={'type': 'date'}),
            'data_fim': forms.DateInput(attrs={'type': 'date'}),
        }

    def clean(self):
        cleaned_data = super().clean()
        data_inicio = cleaned_data.get('data_inicio')
        data_fim = cleaned_data.get('data_fim')
        if data_inicio and data_fim and data_fim < data_inicio:
            raise forms.ValidationError('A data final não pode ser antes da data inicial.')
        return cleaned_data


class FuncionarioForm(forms.ModelForm):
    class Meta:
        model = Funcionario
        fields = [
            'nome', 'matricula', 'cargo', 'setor', 'status', 'cpf', 'telefone', 'endereco', 'foto',
            'sexo', 'estado_civil', 'tem_filho_menor_idade',
            'email', 'banco', 'agencia', 'numero_conta',
        ]

class PreCadastroFuncionarioForm(forms.ModelForm):
    class Meta:
        model = Funcionario
        fields = ['nome', 'cpf', 'cargo', 'setor', 'data_admissao']
        widgets = {
            'data_admissao': forms.DateInput(attrs={'type': 'date'}),
        }

    def clean_cpf(self):
        cpf = ''.join(filter(str.isdigit, self.cleaned_data['cpf']))
        if len(cpf) != 11:
            raise forms.ValidationError('CPF deve ter 11 dígitos.')
        if Funcionario.objects.filter(cpf=cpf).exists():
            raise forms.ValidationError('Já existe um funcionário cadastrado com este CPF.')
        return cpf

class SolicitarFeriasForm(forms.ModelForm):
    data_solicitada = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        label='Data de início desejada',
    )

    class Meta:
        model = SolicitacaoFerias
        fields = ['data_solicitada', 'dias_solicitados']

    def clean_data_solicitada(self):
        from datetime import timedelta
        from django.utils import timezone
        data = self.cleaned_data['data_solicitada']
        minimo = timezone.now().date() + timedelta(days=30)
        if data < minimo:
            raise forms.ValidationError(
                'A data de início das férias deve ser pelo menos 30 dias após hoje.'
            )
        return data


class ProporOutraDataForm(forms.Form):
    data_solicitada = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        label='Nova data desejada',
    )
    dias_solicitados = forms.IntegerField(min_value=1, label='Quantidade de dias')

    def clean_data_solicitada(self):
        from datetime import timedelta
        from django.utils import timezone
        data = self.cleaned_data['data_solicitada']
        minimo = timezone.now().date() + timedelta(days=30)
        if data < minimo:
            raise forms.ValidationError(
                'A data de início das férias deve ser pelo menos 30 dias após hoje.'
            )
        return data


class SugerirAlteracaoForm(forms.Form):
    data_sugerida = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        label='Data sugerida',
    )
    dias_sugeridos = forms.IntegerField(min_value=1, label='Dias sugeridos')
    observacao_gestor = forms.CharField(widget=forms.Textarea, required=False, label='Observação')
    
class AvisoFeriasForm(forms.Form):
    data_prevista = forms.DateField(
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        input_formats=['%Y-%m-%d'],
        label='Data prevista para as férias',
    )
    mensagem = forms.CharField(widget=forms.Textarea, required=False, label='Mensagem (opcional)')
    
class TermoComodatoForm(forms.ModelForm):
    data_emprestimo = forms.DateField(
        widget=forms.DateInput(attrs={'type':'date'}, format='%Y-%m-%d'),
        label='Data do empréstimo',
    )

    class Meta:
        model = TermoComodato
        fields = ['funcionario','modalidade','data_emprestimo']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['funcionario'].queryset = Funcionario.objects.exclude(status='inativo').order_by('nome')


class ItemComodatoForm(forms.ModelForm):
    numero_serie = forms.CharField(required=False, max_length=50, label='Nº de série / IMEI')
    descricao = forms.CharField(
        label='Detalhes (cor, modelo, etc.)',
        required=False,
        max_length=200,
    )

    class Meta:
        model = ItemComodato
        fields = ['item_catalogo', 'descricao', 'numero_serie', 'tamanho', 'quantidade', 'valor_unitario']
        labels = {'item_catalogo': 'Item'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['item_catalogo'].queryset = ItemCatalogoComodato.objects.filter(ativo=True)

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('DELETE'):
            return cleaned_data

        # Linhas extras intocadas nem chegam aqui (o formset as ignora); se a linha
        # foi preenchida, o item do catálogo é obrigatório.
        if not cleaned_data.get('item_catalogo'):
            self.add_error('item_catalogo', 'Selecione o item.')
        return cleaned_data


class BaseItemComodatoFormSet(BaseInlineFormSet):
    # A view define antes do is_valid(), a partir do form do cabeçalho.
    modalidade = None

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        linhas = [
            f for f in self.forms
            if f.has_changed() and not self._should_delete_form(f)
        ]
        if not linhas:
            raise forms.ValidationError('Adicione pelo menos um item ao comodato.')

        if not self.modalidade:
            return

        for f in linhas:
            item = f.cleaned_data.get('item_catalogo')
            if item and item.modalidade != self.modalidade:
                f.add_error('item_catalogo', 'Este item não pertence à modalidade escolhida.')

            # Tamanho só vale para uniforme e nº de série só para equipamento
            # (valor vale para os dois): descarta o que não se aplica, mesmo que tenha vindo no POST.
            if self.modalidade == 'uniforme':
                f.cleaned_data['numero_serie'] = ''
                f.instance.numero_serie = ''
            else:
                f.cleaned_data['tamanho'] = ''
                f.instance.tamanho = ''

                # Um nº de série identifica um único aparelho.
                if f.cleaned_data.get('numero_serie') and f.cleaned_data.get('quantidade') != 1:
                    f.add_error('quantidade', 'Com nº de série, a quantidade deve ser 1 (use uma linha por aparelho).')


ItemComodatoFormSet = inlineformset_factory(
        TermoComodato,
        ItemComodato,
        form=ItemComodatoForm,
        formset=BaseItemComodatoFormSet,
        fields=['item_catalogo', 'descricao', 'numero_serie', 'tamanho', 'quantidade', 'valor_unitario'],
        extra=5,
        can_delete=True,
    )
