from django.db import models

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
    
from django.contrib.auth.models import User


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
