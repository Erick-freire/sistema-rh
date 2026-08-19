"""
Cliente HTTP cru para a API de REP dos relógios Control iD iDClass
(https://www.controlid.com.br/suporte/api_idclass_latest.html).

Este é o firmware de "Registrador Eletrônico de Ponto" (REP), diferente da API
genérica de controle de acesso (load_objects/create_objects) documentada em
controlid.com.br/docs/access-api-en -- aquela API não existe neste firmware
("Invalid command"), confirmado ao testar contra os equipamentos reais da
Sotrigo.

Todo o protocolo abaixo foi validado ao vivo contra o relógio "PONTO
SALGADINHO" (192.168.0.208): login, listar usuários, criar/atualizar/remover
um usuário de teste, e ler marcações via get_afd.
"""
from datetime import datetime

import requests
import urllib3
from django.utils import timezone
from django.utils.dateparse import parse_datetime

# Os equipamentos da Sotrigo servem a API em HTTPS com certificado autoassinado
# (porta 80 só redireciona para 443) -- não há CA própria para validar, então
# desligamos a verificação de certificado e silenciamos o aviso correspondente.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TIMEOUT_CONEXAO = 5
# get_afd pode devolver bastante texto de uma vez -- em equipamentos com muita
# gente cadastrada (ex.: relógio da Farinha, 641 cadastros) já vimos o
# get_afd levar mais de 100s pra responder, então o antigo limite de 30s
# dava timeout antes de terminar.
TIMEOUT_LEITURA = 150

MODE_671 = '671'  # ativa o dialeto CPF (em vez de PIS) exigido pela Portaria 671

# Leiaute oficial do AFD (Portaria 1510/671 do MTE): cada linha é um registro de
# um tipo (posição 10, 1 caractere) -- só o tipo "3" (marcação de ponto REP-C/
# REP-A) nos interessa aqui; os demais (1=cabeçalho, 2/5=cadastro de empregado,
# 4=ajuste de relógio, 6=evento, 9=rodapé) são ignorados.
# Dois firmwares em uso na Sotrigo codificam a data/hora da marcação (campo 3)
# de formas diferentes -- detectamos qual pelo tamanho total da linha:
TAMANHO_REGISTRO_PONTO_ISO = 50       # NSR(9)+TIPO(1)+DH ISO "AAAA-MM-ddThh:mm:00ZZZZZ"(24)+CPF/PIS(12)+CRC(4)
TAMANHO_REGISTRO_PONTO_COMPACTO = 38  # NSR(9)+TIPO(1)+DH compacta "DDMMAAAAHHMM"(12)+CPF/PIS(12)+CRC(4)


class ControlIdAPIError(Exception):
    """Erro ao comunicar com a API REST de um equipamento Control iD."""


class ControlIdTimeoutError(ControlIdAPIError):
    """Equipamento não respondeu a tempo (provavelmente offline)."""


class ControlIdUsuarioNaoEncontradoError(ControlIdAPIError):
    """update_users.fcgi para um CPF que ainda não existe no equipamento."""


def _base_url(equipamento):
    protocolo = 'https' if equipamento.usa_https else 'http'
    return f'{protocolo}://{equipamento.ip}:{equipamento.porta}'


def _post(equipamento, comando, payload, session=None, mode=None, esperar_texto=False):
    url = f'{_base_url(equipamento)}/{comando}'
    params = {}
    if session:
        params['session'] = session
    if mode:
        params['mode'] = mode

    try:
        resposta = requests.post(
            url,
            json=payload,
            params=params,
            timeout=(TIMEOUT_CONEXAO, TIMEOUT_LEITURA),
            verify=False,
        )
    except requests.exceptions.Timeout as erro:
        raise ControlIdTimeoutError(f'Equipamento {equipamento} não respondeu a tempo.') from erro
    except requests.exceptions.ConnectionError as erro:
        raise ControlIdTimeoutError(f'Não foi possível conectar ao equipamento {equipamento}.') from erro

    if esperar_texto:
        if not resposta.ok:
            raise ControlIdAPIError(
                f'Falha na API do equipamento {equipamento} (HTTP {resposta.status_code}): {resposta.text[:300]}'
            )
        return resposta.text

    try:
        corpo = resposta.json()
    except ValueError as erro:
        raise ControlIdAPIError(f'Resposta inválida do equipamento {equipamento}.') from erro

    if not resposta.ok:
        mensagem = corpo.get('error', resposta.text[:300]) if isinstance(corpo, dict) else resposta.text[:300]
        if mensagem == 'Usuário não encontrado':
            raise ControlIdUsuarioNaoEncontradoError(mensagem)
        raise ControlIdAPIError(f'Erro retornado pelo equipamento {equipamento}: {mensagem}')

    return corpo


def login(equipamento):
    """Autentica no equipamento e retorna o token de sessão."""
    corpo = _post(equipamento, 'login.fcgi', {
        'login': equipamento.usuario_admin,
        'password': equipamento.senha_admin,
    })

    session = corpo.get('session')
    if not session:
        raise ControlIdAPIError(f'Login sem sessão retornada pelo equipamento {equipamento}.')

    return session


def get_about(equipamento, session):
    """Identificação do equipamento: número de série, firmware, se é modelo facial."""
    return _post(equipamento, 'get_about.fcgi', {}, session=session)


def load_users(equipamento, session, cpfs=None, limit=100, offset=0):
    payload = {'limit': limit, 'offset': offset}
    if cpfs:
        payload['users_cpf'] = [int(cpf) for cpf in cpfs]

    corpo = _post(equipamento, 'load_users.fcgi', payload, session=session, mode=MODE_671)
    return corpo.get('users', [])


def buscar_usuario_por_cpf(equipamento, session, cpf):
    usuarios = load_users(equipamento, session, cpfs=[cpf], limit=1, offset=0)
    return usuarios[0] if usuarios else None


def add_user(equipamento, session, cpf, nome):
    _post(equipamento, 'add_users.fcgi', {
        'users': [{'cpf': int(cpf), 'name': nome}],
    }, session=session, mode=MODE_671)


def update_user(equipamento, session, cpf, nome):
    """Levanta ControlIdUsuarioNaoEncontradoError se o CPF ainda não existe no equipamento."""
    _post(equipamento, 'update_users.fcgi', {
        'users': [{'cpf': int(cpf), 'name': nome}],
    }, session=session, mode=MODE_671)


def remove_user(equipamento, session, cpf):
    # Formato confirmado ao vivo: lista simples de CPFs, não uma lista de objetos
    # (diferente de add_users/update_users) -- {"users": [cpf, ...]}.
    _post(equipamento, 'remove_users.fcgi', {
        'users': [int(cpf)],
    }, session=session, mode=MODE_671)


def get_afd(equipamento, session, nsr_inicial):
    """Texto bruto (não-JSON) com as marcações desde `nsr_inicial` (exclusive)."""
    return _post(equipamento, 'get_afd.fcgi', {
        'initial_nsr': nsr_inicial,
    }, session=session, mode=MODE_671, esperar_texto=True)


def _parse_data_hora_compacta(valor):
    """Formato DDMMAAAAHHMM (12 posições), visto no equipamento PONTO ADM."""
    try:
        dia, mes, ano = int(valor[0:2]), int(valor[2:4]), int(valor[4:8])
        hora, minuto = int(valor[8:10]), int(valor[10:12])
        return timezone.make_aware(datetime(ano, mes, dia, hora, minuto))
    except (ValueError, IndexError):
        return None


def parse_afd(texto):
    """
    O AFD tem várias linhas de tipos diferentes (leiaute oficial da Portaria
    1510/671 do MTE) -- só extraímos o tipo "3" (marcação de ponto REP-C/
    REP-A), identificado pelo dígito na posição 10. Tipos 1/2/4/5/6/9
    (cabeçalho, cadastro de empregado, ajuste de relógio, evento, rodapé) são
    ignorados aqui.

    Dois firmwares em uso na Sotrigo codificam a data/hora de forma diferente
    (confirmado ao vivo): PONTO SALGADINHO usa ISO completo (linha de 50
    caracteres), PONTO ADM usa uma versão compacta (linha de 38 caracteres).
    Detectamos qual pelo tamanho total da linha.
    """
    registros = []

    for linha in texto.splitlines():
        if len(linha) < 10 or not linha[:9].isdigit() or linha[9] != '3':
            continue

        nsr = int(linha[0:9])

        if len(linha) == TAMANHO_REGISTRO_PONTO_ISO:
            momento = parse_datetime(linha[10:34])
            identificador_completo = linha[34:46]
            crc = linha[46:50]
        elif len(linha) == TAMANHO_REGISTRO_PONTO_COMPACTO:
            momento = _parse_data_hora_compacta(linha[10:22])
            identificador_completo = linha[22:34]
            crc = linha[34:38]
        else:
            continue  # tamanho de linha não reconhecido para tipo "3" -- ignora com segurança

        if momento is None:
            continue

        registros.append({
            'nsr': nsr,
            'momento': momento,
            'prefixo': identificador_completo[0:1],
            'identificador': identificador_completo[1:12],
            'crc': crc,
            'linha_bruta': linha,
        })

    return registros
