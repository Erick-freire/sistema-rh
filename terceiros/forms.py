from django import forms
from django.contrib.auth.models import User
from django.utils import timezone

from funcionarios.arquivos import ACCEPT_DOCUMENTO, validar_upload

from .models import (
    MESES,
    ClausulaContratual,
    ColaboradorTerceiro,
    ContratoTerceiro,
    DocumentoColaborador,
    DocumentoEmpresaTerceira,
    EmpresaTerceira,
    ResponsavelTerceiro,
    so_digitos,
)

ESTADOS = [
    'AC', 'AL', 'AP', 'AM', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MT', 'MS', 'MG',
    'PA', 'PB', 'PR', 'PE', 'PI', 'RJ', 'RN', 'RS', 'RO', 'RR', 'SC', 'SP', 'SE', 'TO',
]


class DadosEmpresaForm(forms.ModelForm):
    """
    Dados cadastrais da prestadora. Serve o cadastro inicial feito pelo RH
    (via `CadastroEmpresaForm`) e a edição feita pelo próprio responsável na
    aba "Minha Empresa".
    """

    # CNPJ e CEP são normalizados para dígitos no clean, mas o `max_length` do
    # model roda antes disso e barraria a entrada formatada ("75.132-160" tem 10
    # caracteres contra os 9 do campo). Por isso a folga aqui.
    cnpj = forms.CharField(max_length=20, label='CNPJ')
    cep = forms.CharField(max_length=12, label='CEP')

    class Meta:
        model = EmpresaTerceira
        fields = [
            'razao_social', 'nome_fantasia', 'cnpj',
            'logradouro', 'numero', 'bairro', 'cidade', 'estado', 'cep',
        ]
        widgets = {
            'estado': forms.Select(choices=[('', '—')] + [(uf, uf) for uf in ESTADOS]),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Fechado o cadastro inicial, o CNPJ trava: o contrato já foi gerado com
        # ele, e mudar aqui deixaria o PDF emitido divergindo do cadastro. Quem
        # corrige é o RH. `disabled` é o que garante isso de fato -- o Django
        # descarta o valor que vier no POST e reusa o que está no banco, então
        # não adianta mexer no HTML.
        if self.cnpj_travado():
            self.fields['cnpj'].disabled = True

    def cnpj_travado(self):
        return bool(self.instance.pk) and self.instance.status != 'pendente_cadastro'

    def clean_cnpj(self):
        cnpj = so_digitos(self.cleaned_data.get('cnpj'))
        if len(cnpj) != 14:
            raise forms.ValidationError('CNPJ deve ter 14 dígitos.')
        # O unique=True do model já barra no banco; aqui a mensagem é amigável e
        # aponta qual empresa já ocupa o CNPJ.
        existente = EmpresaTerceira.objects.filter(cnpj=cnpj).exclude(pk=self.instance.pk).first()
        if existente is not None:
            raise forms.ValidationError(
                f'Este CNPJ já está cadastrado para "{existente.razao_social}".'
            )
        return cnpj

    def clean_cep(self):
        cep = so_digitos(self.cleaned_data.get('cep'))
        if len(cep) != 8:
            raise forms.ValidationError('CEP deve ter 8 dígitos.')
        return cep


class CadastroEmpresaForm(DadosEmpresaForm):
    """
    Cadastro inicial feito pelo RH: dados da empresa e o mínimo do responsável
    (nome + CPF, que vira o login). O resto dos dados do representante legal é
    preenchido pelo próprio responsável no primeiro acesso.
    """

    responsavel_nome = forms.CharField(max_length=200, label='Nome do responsável')
    responsavel_cpf = forms.CharField(max_length=18, label='CPF do responsável')

    def clean_responsavel_cpf(self):
        cpf = so_digitos(self.cleaned_data.get('responsavel_cpf'))
        if len(cpf) != 11:
            raise forms.ValidationError('CPF deve ter 11 dígitos.')
        if ResponsavelTerceiro.objects.filter(cpf=cpf).exists():
            raise forms.ValidationError('Já existe um responsável cadastrado com este CPF.')
        # O CPF vira o username, e o mesmo CPF já pode estar em uso por um
        # colaborador CLT -- barrar aqui evita estourar no create_user.
        if User.objects.filter(username=cpf).exists():
            raise forms.ValidationError('Este CPF já é usado como login no sistema.')
        return cpf


class DadosResponsavelForm(forms.ModelForm):
    """Completado pelo próprio responsável: o que o contrato exige dele."""

    class Meta:
        model = ResponsavelTerceiro
        fields = ['nome', 'rg', 'estado_civil', 'profissao', 'endereco']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in ('rg', 'estado_civil', 'profissao', 'endereco'):
            self.fields[campo].required = True


class ContratoTerceiroForm(forms.ModelForm):
    salvar_avulsas_na_biblioteca = forms.BooleanField(
        required=False,
        label='Guardar as cláusulas avulsas na biblioteca para reaproveitar depois',
    )
    titulo_avulsas = forms.CharField(
        max_length=200,
        required=False,
        label='Título para guardar na biblioteca',
    )

    class Meta:
        model = ContratoTerceiro
        fields = [
            'objeto_servico', 'composicao_equipe', 'condicoes_remuneracao', 'prazo_meses',
            'clausulas_biblioteca', 'clausulas_avulsas',
        ]
        widgets = {
            'objeto_servico': forms.Textarea(attrs={'rows': 4}),
            'composicao_equipe': forms.Textarea(attrs={'rows': 4}),
            'condicoes_remuneracao': forms.Textarea(attrs={'rows': 4}),
            'clausulas_avulsas': forms.Textarea(attrs={'rows': 6}),
            'clausulas_biblioteca': forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['clausulas_biblioteca'].queryset = ClausulaContratual.objects.filter(ativa=True)
        # Todas as cláusulas ativas vêm marcadas por padrão: o modelo real é um
        # texto praticamente fixo, e é mais rápido o RH desmarcar exceções do
        # que marcar trinta itens a cada contrato.
        if not self.is_bound:
            self.initial.setdefault(
                'clausulas_biblioteca',
                list(self.fields['clausulas_biblioteca'].queryset.values_list('pk', flat=True)),
            )

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('salvar_avulsas_na_biblioteca'):
            if not (cleaned_data.get('clausulas_avulsas') or '').strip():
                self.add_error(
                    'salvar_avulsas_na_biblioteca',
                    'Não há texto avulso para guardar na biblioteca.',
                )
            if not (cleaned_data.get('titulo_avulsas') or '').strip():
                self.add_error('titulo_avulsas', 'Informe um título para guardar na biblioteca.')
        return cleaned_data


class ClausulaContratualForm(forms.ModelForm):
    class Meta:
        model = ClausulaContratual
        fields = ['titulo', 'texto', 'ordem_sugerida', 'ativa']
        widgets = {
            'texto': forms.Textarea(attrs={'rows': 8}),
        }


class ColaboradorTerceiroForm(forms.ModelForm):
    class Meta:
        model = ColaboradorTerceiro
        fields = ['nome', 'cpf', 'funcao']

    def __init__(self, *args, empresa=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.empresa = empresa

    def clean_cpf(self):
        cpf = so_digitos(self.cleaned_data.get('cpf'))
        if len(cpf) != 11:
            raise forms.ValidationError('CPF deve ter 11 dígitos.')
        if self.empresa is not None:
            duplicado = (
                ColaboradorTerceiro.objects
                .filter(empresa=self.empresa, cpf=cpf)
                .exclude(pk=self.instance.pk)
                .exists()
            )
            if duplicado:
                raise forms.ValidationError('Este CPF já está cadastrado para esta empresa.')
        return cpf


class _DocumentoComCompetenciaForm(forms.ModelForm):
    """
    Base dos dois formulários de envio. A competência é obrigatória ou não
    conforme o tipo escolhido, e isso só dá para decidir depois de saber o tipo
    -- por isso a checagem fica no `clean`, e não em `required` no campo.
    """

    ANOS_PARA_TRAS = 3

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        ano_atual = timezone.now().year
        anos = list(range(ano_atual, ano_atual - self.ANOS_PARA_TRAS - 1, -1))
        self.fields['competencia_ano'].widget = forms.Select(
            choices=[('', '—')] + [(ano, ano) for ano in anos]
        )
        self.fields['competencia_mes'].widget = forms.Select(
            choices=[('', '—')] + list(MESES)
        )
        self.fields['arquivo'].widget.attrs['accept'] = ACCEPT_DOCUMENTO

    def clean_arquivo(self):
        return validar_upload(self.cleaned_data.get('arquivo'))

    def _validar_competencia(self, cleaned_data, exige_competencia):
        mes = cleaned_data.get('competencia_mes')
        ano = cleaned_data.get('competencia_ano')

        if exige_competencia:
            if not mes:
                self.add_error('competencia_mes', 'Informe o mês de competência.')
            if not ano:
                self.add_error('competencia_ano', 'Informe o ano de competência.')
        else:
            # Um tipo não-mensal nunca guarda competência, mesmo que o campo
            # tenha vindo preenchido de uma troca de tipo no formulário.
            cleaned_data['competencia_mes'] = None
            cleaned_data['competencia_ano'] = None

        return cleaned_data


class EnviarDocumentoColaboradorForm(_DocumentoComCompetenciaForm):
    class Meta:
        model = DocumentoColaborador
        fields = ['tipo', 'arquivo', 'competencia_mes', 'competencia_ano']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['competencia_mes'].required = False
        self.fields['competencia_ano'].required = False

    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get('tipo')
        return self._validar_competencia(
            cleaned_data, exige_competencia=tipo in DocumentoColaborador.TIPOS_MENSAIS
        )


class EnviarDocumentoEmpresaForm(_DocumentoComCompetenciaForm):
    """FGTS e DCTFWeb: a competência é sempre obrigatória."""

    class Meta:
        model = DocumentoEmpresaTerceira
        fields = ['tipo', 'arquivo', 'competencia_mes', 'competencia_ano']

    def clean(self):
        cleaned_data = super().clean()
        return self._validar_competencia(cleaned_data, exige_competencia=True)


class RejeitarDocumentoTerceiroForm(forms.Form):
    motivo = forms.CharField(widget=forms.Textarea, required=True, label='Motivo da rejeição')
