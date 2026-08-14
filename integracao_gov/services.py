from django.core.files.base import ContentFile
from django.utils import timezone

from . import client
from .models import SolicitacaoAssinatura


def enviar_para_gov_br(solicitacao, access_token):
    """
    Envia o arquivo de uma SolicitacaoAssinatura já criada para a API de assinatura.
    Reaproveitada tanto quando o colaborador já tinha um token válido quanto
    logo após concluir o login (callback_gov_view).
    """
    solicitacao.status = 'enviado'
    solicitacao.save()

    try:
        resposta = client.enviar_documento_para_assinatura(access_token, solicitacao.arquivo_original)
        solicitacao.protocolo_gov = resposta.get('protocolo')
        solicitacao.status = 'pendente_assinatura'
        solicitacao.save()
        return True
    except client.GovBrAPIError as erro:
        solicitacao.status = 'erro'
        solicitacao.mensagem_erro = str(erro)
        solicitacao.save()
        return False


def atualizar_status_solicitacao(solicitacao, novo_status, url_arquivo_assinado=None):
    """Aplica uma mudança de status vinda do webhook ou do polling, comum aos dois fluxos."""
    if novo_status not in dict(SolicitacaoAssinatura.STATUS_CHOICES):
        return

    solicitacao.status = novo_status

    if novo_status == 'assinado':
        solicitacao.assinado_em = timezone.now()

        if url_arquivo_assinado:
            try:
                conteudo = client.baixar_arquivo_assinado(url_arquivo_assinado)
                nome_arquivo = f'assinado_{solicitacao.documento_id}_{solicitacao.id}.pdf'
                solicitacao.arquivo_assinado.save(nome_arquivo, ContentFile(conteudo), save=False)
            except client.GovBrAPIError as erro:
                solicitacao.mensagem_erro = str(erro)

    solicitacao.save()


def verificar_status_pendentes():
    """
    Usado pelo management command de polling — só é necessário se a API do gov.br não
    suportar webhook de status (ver especificação, seção 5.2 e 8).
    """
    pendentes = SolicitacaoAssinatura.objects.filter(status='pendente_assinatura')
    atualizados = 0

    for solicitacao in pendentes:
        try:
            resposta = client.consultar_status_assinatura(solicitacao.protocolo_gov)
        except client.GovBrAPIError as erro:
            solicitacao.status = 'erro'
            solicitacao.mensagem_erro = str(erro)
            solicitacao.save()
            continue

        novo_status = resposta.get('status')
        if novo_status and novo_status != solicitacao.status:
            atualizar_status_solicitacao(solicitacao, novo_status, resposta.get('url_arquivo_assinado'))
            atualizados += 1

    return atualizados
