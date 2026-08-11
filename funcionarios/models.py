from django.db import models
from django.contrib.auth.models import User

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

    nome = models.CharField(max_length=150)
    matricula = models.CharField(max_length=20, unique=True)
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
    cpf = models.CharField(max_length=11, unique=True)
    telefone = models.CharField(max_length=20, blank=True)
    endereco = models.CharField(max_length=255, blank=True)
    foto = models.ImageField(upload_to='fotos_perfil/', blank=True, null=True)

    def __str__(self):
        return f'{self.nome} ({self.matricula})'

    def cadastro_completo(self):
        tipos_obrigatorios = TipoDocumento.objects.filter(obrigatorio=True)
        tipos_enviados = self.documentos.values_list('tipo_documento_id', flat=True)

        for tipo in tipos_obrigatorios:
            if tipo.id not in tipos_enviados:
                return False

        return True
    
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
    senha_provisoria = models.BooleanField(default=False)

    def __str__(self):
        return f'{self.user.username} ({self.papel})'
    
class TipoDocumento(models.Model):
    nome = models.CharField(max_length=100)
    obrigatorio = models.BooleanField(default=False)
    def __str__(self):
        return self.nome


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
