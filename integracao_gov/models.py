from django.conf import settings
from django.db import models
from django.utils import timezone

from funcionarios.models import Funcionario

from .fields import EncryptedTextField


class TokenGovBr(models.Model):
    # FK para o User do Django (não funcionarios.Usuario): o login gov.br autentica
    # a sessão do request.user diretamente, e nem todo User de teste precisa ter um
    # Usuario/papel associado para exercitar o fluxo OAuth.
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='token_govbr',
    )
    access_token = EncryptedTextField()
    refresh_token = EncryptedTextField(blank=True, default='')
    expira_em = models.DateTimeField()
    criado_em = models.DateTimeField(auto_now_add=True)

    def expirado(self):
        return timezone.now() >= self.expira_em

    def __str__(self):
        return f'Token gov.br de {self.usuario.username}'


class SolicitacaoAssinatura(models.Model):
    STATUS_CHOICES = [
        ('aguardando_login', 'Aguardando login no gov.br'),
        ('enviado', 'Enviado para assinatura'),
        ('pendente_assinatura', 'Pendente de assinatura'),
        ('assinado', 'Assinado'),
        ('rejeitado', 'Rejeitado'),
        ('erro', 'Erro'),
    ]

    # Sem FK para funcionarios.Documento: o arquivo enviado aqui (ex.: contrato recebido
    # por WhatsApp) ainda não existe como Documento no sistema. Só vira um Documento depois,
    # quando o colaborador baixa o arquivo assinado e o envia manualmente em "Enviar Documento".
    funcionario = models.ForeignKey(
        Funcionario,
        on_delete=models.CASCADE,
        related_name='solicitacoes_assinatura',
    )
    arquivo_original = models.FileField(upload_to='assinaturas_pendentes/%Y/%m/')
    protocolo_gov = models.CharField(max_length=100, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='aguardando_login')
    solicitado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
    )
    solicitado_em = models.DateTimeField(auto_now_add=True)
    assinado_em = models.DateTimeField(null=True, blank=True)
    arquivo_assinado = models.FileField(upload_to='documentos_assinados/%Y/%m/', null=True, blank=True)
    mensagem_erro = models.TextField(blank=True)

    class Meta:
        ordering = ['-solicitado_em']

    def __str__(self):
        return f'{self.funcionario.nome} ({self.get_status_display()})'
