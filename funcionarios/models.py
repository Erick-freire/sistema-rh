import os

from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class Setor(models.Model):
    nome = models.CharField(max_length=100)
    gestor = models.ForeignKey(
        'Funcionario',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='setores_gerenciados'
    )

    def __str__(self):
        return self.nome
    
class Funcionario(models.Model):
    STATUS_CHOICES = [
        ('ativo', 'Ativo'),
        ('inativo', 'Inativo'),
        ('pendente', 'pendente'),
    ]
    
    SEXO_CHOICES = [
        ('masculino', 'masculino'),
        ('feminino', 'feminino'),
        ('não-binário', 'não-binário')
    ]

    ESTADO_CIVIL_CHOICES = [
        ('solteiro', 'Solteiro(a)'),
        ('casado', 'Casado(a)'),
        ('uniao_estavel', 'União Estável'),
        ('divorciado', 'Divorciado(a)'),
        ('viuvo', 'Viúvo(a)'),
    ]

    CAMPOS_PESSOAIS_OBRIGATORIOS = [
        'cpf', 'email', 'banco', 'agencia', 'numero_conta', 'sexo', 'estado_civil',
    ]

    nome = models.CharField(max_length=150)
    matricula = models.CharField(max_length=20, unique=True, null=True, blank=True)
    sexo = models.CharField(
        max_length=15,
        choices=SEXO_CHOICES,
        blank=True
    )
    estado_civil = models.CharField(
        max_length=15,
        choices=ESTADO_CIVIL_CHOICES,
        blank=True
    )
    tem_filho_menor_idade = models.BooleanField(
        default=False,
        help_text='Marque se o funcionário possui filho(a) menor de 18 anos.'
    )
    cargo = models.CharField(max_length=100)
    data_admissao = models.DateField(null=True, blank=True)
    setor = models.ForeignKey(
        Setor,
        on_delete=models.PROTECT,
        related_name='funcionarios'
    )
    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default='ativo'
    )
    cpf = models.CharField(
        max_length=11,
        unique=True,
        help_text='Usado como login do colaborador.'
    )
    telefone = models.CharField(max_length=20, blank=True)
    endereco = models.CharField(max_length=255, blank=True)
    email = models.EmailField(
        max_length=254,
        blank=True,
        help_text='Usado para envio do holerite.'
    )
    banco = models.CharField(max_length=100, blank=True)
    agencia = models.CharField(max_length=10, blank=True)
    numero_conta = models.CharField(max_length=20, blank=True)
    foto = models.ImageField(upload_to='fotos_perfil/', blank=True, null=True)

    def __str__(self):
        return f'{self.nome} ({self.matricula})'

    @property
    def iniciais(self):
        partes = self.nome.split()
        if not partes:
            return ''
        if len(partes) == 1:
            return partes[0][:2].upper()
        return (partes[0][0] + partes[-1][0]).upper()

    @property
    def primeiro_nome(self):
        partes = self.nome.split()
        return partes[0] if partes else self.nome

    def dados_pessoais_completos(self):
        return all(getattr(self, campo) for campo in self.CAMPOS_PESSOAIS_OBRIGATORIOS)

    def tipos_documentos_obrigatorios(self):
        return [
            tipo for tipo in TipoDocumento.objects.filter(obrigatorio=True)
            if tipo.aplica_a(self)
        ]

    def cadastro_completo(self):
        if not self.dados_pessoais_completos():
            return False

        tipos_obrigatorios = self.tipos_documentos_obrigatorios()
        tipos_enviados = self.documentos.values_list('tipo_documento_id', flat=True)

        for tipo in tipos_obrigatorios:
            if tipo.id not in tipos_enviados:
                return False

        return True

    def _somar_anos(self, data, anos):
        try:
            return data.replace(year=data.year + anos)
        except ValueError:
            return data.replace(year=data.year + anos, day=28)

    def _periodo_ferias_atual(self):
        if self.data_admissao is None:
            return None

        hoje = timezone.now().date()
        solicitacoes = list(
            self.solicitacoes_ferias.filter(status='aprovada')
            .order_by('data_solicitada')
            .values_list('data_solicitada', 'dias_solicitados')
        )
        indice = 0
        n = 0

        while True:
            aquisitivo_inicio = self.data_admissao if n == 0 else self._somar_anos(self.data_admissao, n)
            aquisitivo_fim = self._somar_anos(self.data_admissao, n + 1)
            concessivo_fim = self._somar_anos(aquisitivo_fim, 1)

            dias_gozados = 0
            while indice < len(solicitacoes) and solicitacoes[indice][0] <= concessivo_fim:
                dias_gozados += solicitacoes[indice][1]
                indice += 1
                if dias_gozados >= 30:
                    break

            if dias_gozados >= 30:
                n += 1
                continue

            return {
                'aquisitivo_inicio': aquisitivo_inicio,
                'aquisitivo_fim': aquisitivo_fim,
                'concessivo_fim': concessivo_fim,
                'dias_gozados': dias_gozados,
                'dias_pendentes': max(0, 30 - dias_gozados),
                'vencida': hoje > concessivo_fim,
            }

    def proxima_data_vencimento(self):
        periodo = self._periodo_ferias_atual()
        return periodo['concessivo_fim'] if periodo else None

    def dias_para_vencer_ferias(self):
        periodo = self._periodo_ferias_atual()
        if periodo is None:
            return None
        return (periodo['concessivo_fim'] - timezone.now().date()).days

    def ferias_vencidas(self):
        periodo = self._periodo_ferias_atual()
        return periodo is not None and periodo['vencida']

    def dias_pendentes_ferias(self):
        periodo = self._periodo_ferias_atual()
        return periodo['dias_pendentes'] if periodo else None
    
class Usuario(models.Model):
    PAPEL_CHOICES = [
        ('rh', 'RH'),
        ('gestor', 'Gestor'),
        ('colaborador', 'Colaborador'),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE
    )
    funcionario = models.OneToOneField(
        Funcionario,
        on_delete=models.CASCADE
    )
    papel = models.CharField(
        max_length=15,
        choices=PAPEL_CHOICES
    )
    acesso_rh = models.BooleanField(
        default=False,
        help_text='Concede acesso às telas de RH além do papel principal '
                   '(ex.: colaborador ou gestor que também trabalha no setor de RH).'
    )
    senha_provisoria = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.user.username} ({self.papel})'
    
class TipoDocumento(models.Model):
    CONDICAO_CHOICES = [
        ('sempre', 'Sempre obrigatório'),
        ('sexo_masculino', 'Apenas para funcionários do sexo masculino'),
        ('sexo_feminino', 'Apenas para funcionários do sexo feminino'),
        ('casado', 'Apenas para funcionários casados(as)'),
        ('filho_menor_idade', 'Apenas para funcionários com filho(a) menor de idade'),
    ]

    nome = models.CharField(max_length=100)
    obrigatorio = models.BooleanField(default=False)
    condicao = models.CharField(
        max_length=20,
        choices=CONDICAO_CHOICES,
        default='sempre',
        help_text='Define quando este documento é exigido, além do campo "obrigatório" estar marcado.'
    )

    def __str__(self):
        return self.nome

    def aplica_a(self, funcionario):
        if self.condicao == 'sexo_masculino':
            return funcionario.sexo == 'masculino'
        if self.condicao == 'sexo_feminino':
            return funcionario.sexo == 'feminino'
        if self.condicao == 'casado':
            return funcionario.estado_civil == 'casado'
        if self.condicao == 'filho_menor_idade':
            return funcionario.tem_filho_menor_idade
        return True


class Documento(models.Model):
    funcionario = models.ForeignKey(
        Funcionario,
        on_delete=models.CASCADE,
        related_name='documentos'
    )
    tipo_documento = models.ForeignKey(
        TipoDocumento,
        on_delete=models.PROTECT
    )
    arquivo = models.FileField(upload_to='documentos/%Y/%m/')
    data_upload = models.DateTimeField(auto_now_add=True)
    enviado_por = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True
    )

    def __str__(self):
        return f'{self.tipo_documento} - {self.funcionario}'

    @property
    def nome_arquivo(self):
        return os.path.basename(self.arquivo.name)

def cadastro_completo(self):
        tipos_obrigatorios = TipoDocumento.objects.filter(obrigatorio=True)
        tipos_enviados = self.documentos.values_list('tipo_documento_id', flat=True)

        for tipo in tipos_obrigatorios:
            if tipo.id not in tipos_enviados:
                return False

        return True
    
class SolicitacaoFerias(models.Model):
    STATUS_CHOICES = [
        ('pendente_gestor', 'Aguardando Gestor'),
        ('sugestao_gestor', 'Gestor sugeriu alteração'),
        ('pendente_rh', 'Aguardando RH'),
        ('aprovada', 'Aprovada'),
        ('adiada', 'Adiada pelo colaborador'),
    ]

    funcionario = models.ForeignKey(
        Funcionario,
        on_delete=models.CASCADE,
        related_name='solicitacoes_ferias'
    )
    data_solicitada = models.DateField()
    dias_solicitados = models.PositiveIntegerField(default=30)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pendente_gestor')

    data_sugerida = models.DateField(null=True, blank=True)
    dias_sugeridos = models.PositiveIntegerField(null=True, blank=True)
    observacao_gestor = models.TextField(blank=True)

    analisado_por_gestor = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='ferias_analisadas_gestor'
    )
    concluido_por_rh = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='ferias_concluidas_rh'
    )

    criado_em = models.DateTimeField(auto_now_add=True)
    atualizado_em = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.funcionario.nome} - {self.data_solicitada} ({self.get_status_display()})'

class AvisoFerias(models.Model):
    funcionario = models.ForeignKey(
        Funcionario, on_delete=models.CASCADE, related_name='avisos_ferias'
    )
    data_prevista = models.DateField()
    mensagem = models.TextField(blank=True)
    criado_por = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    criado_em = models.DateTimeField(auto_now_add=True)
    visualizado = models.BooleanField(default=False)
    visualizado_em = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'{self.funcionario.nome} - {self.data_prevista}'