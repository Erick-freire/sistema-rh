import base64
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings


class GovBrAPIError(Exception):
    """Erro ao comunicar com a API do gov.br (login Conecta ou assinatura Assina)."""


def gerar_state():
    return secrets.token_urlsafe(32)


def montar_url_autorizacao(state):
    # Escopos exatos exigidos pela API de Assinatura ainda não confirmados na doc oficial
    # (ver especificação, seção 8) — 'openid profile' é o mínimo padrão do Conecta gov.br.
    parametros = {
        'response_type': 'code',
        'client_id': settings.GOVBR_CLIENT_ID,
        'redirect_uri': settings.GOVBR_REDIRECT_URI,
        'scope': 'openid profile',
        'state': state,
    }
    return f'{settings.GOVBR_AUTHORIZE_URL}?{urlencode(parametros)}'


def trocar_code_por_token(code):
    """
    Troca o `code` do callback por um access_token, servidor-a-servidor (client_secret nunca
    trafega para o navegador). Formato exato da resposta pendente de confirmação (seção 8);
    assume-se o padrão OAuth2 (`access_token`, `refresh_token`, `expires_in`).
    """
    resposta = requests.post(
        settings.GOVBR_TOKEN_URL,
        data={
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': settings.GOVBR_REDIRECT_URI,
            'client_id': settings.GOVBR_CLIENT_ID,
            'client_secret': settings.GOVBR_CLIENT_SECRET,
        },
        timeout=15,
    )

    if not resposta.ok:
        raise GovBrAPIError(f'Falha ao trocar code por token (HTTP {resposta.status_code}).')

    return resposta.json()


def enviar_documento_para_assinatura(access_token, arquivo):
    """
    Envia o arquivo para a API de Assinatura gov.br.
    Formato exato do payload (nomes de campo, base64 vs multipart) pendente de confirmação
    na doc oficial (seção 8) — abaixo assume-se JSON com o PDF em base64.
    """
    with arquivo.open('rb') as conteudo:
        conteudo_base64 = base64.b64encode(conteudo.read()).decode()

    resposta = requests.post(
        f'{settings.GOVBR_ASSINATURA_BASE_URL}/assinaturas',
        headers={'Authorization': f'Bearer {access_token}'},
        json={
            'arquivo_base64': conteudo_base64,
            'nome_arquivo': arquivo.name,
        },
        timeout=30,
    )

    if not resposta.ok:
        raise GovBrAPIError(f'Falha ao enviar documento para assinatura (HTTP {resposta.status_code}).')

    return resposta.json()


def consultar_status_assinatura(protocolo_gov):
    resposta = requests.get(
        f'{settings.GOVBR_ASSINATURA_BASE_URL}/assinaturas/{protocolo_gov}',
        timeout=15,
    )

    if not resposta.ok:
        raise GovBrAPIError(f'Falha ao consultar status da assinatura (HTTP {resposta.status_code}).')

    return resposta.json()


def baixar_arquivo_assinado(url_arquivo):
    resposta = requests.get(url_arquivo, timeout=30)

    if not resposta.ok:
        raise GovBrAPIError(f'Falha ao baixar arquivo assinado (HTTP {resposta.status_code}).')

    return resposta.content
