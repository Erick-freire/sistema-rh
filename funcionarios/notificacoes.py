"""
Notificações push do PWA.

As views chamam as funções de evento lá do fim (ex.: `ferias_aguardando_gestor`)
depois de salvar a mudança. O envio fica para depois do commit -- se a
transação desfizer, ninguém é avisado de algo que não aconteceu -- e roda numa
thread, para a tela não esperar a resposta do Google/Apple. Falha de envio
nunca derruba a view: só vai para o log.

Quem recebe segue as mesmas regras das telas onde a pessoa age: gestor pelo
`Usuario.papel` + setor (como `gestor_listar_ferias_view`), RH/DP por
`eh_dp_ou_rh`. Notificação para quem não pode agir sobre ela só faz barulho.
"""
import json
import logging
import threading
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth.models import User
from django.db import connections, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from pywebpush import WebPushException, webpush

from .models import InscricaoPush

logger = logging.getLogger(__name__)

# Só os serviços de push dos navegadores. O endpoint vem do navegador do
# usuário: sem essa lista, qualquer um logado poderia cadastrar uma URL da
# rede interna e fazer o servidor mandar POST para ela.
HOSTS_PUSH_PERMITIDOS = (
    'fcm.googleapis.com',            # Chrome, Edge Android, Samsung
    'android.googleapis.com',
    'push.services.mozilla.com',     # Firefox
    'push.apple.com',                # Safari / PWA no iPhone
    'notify.windows.com',            # Edge no Windows
)

# Por quanto tempo o serviço de push guarda a notificação para o aparelho que
# está desligado ou sem rede.
VALIDADE_SEGUNDOS = 24 * 60 * 60


def push_configurado():
    return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)


def endpoint_permitido(endpoint):
    partes = urlsplit(endpoint or '')
    host = (partes.hostname or '').lower()
    return partes.scheme == 'https' and any(
        host == permitido or host.endswith('.' + permitido) for permitido in HOSTS_PUSH_PERMITIDOS
    )


def notificar(usuarios, titulo, corpo, url, tag='', exceto=None, em_segundo_plano=True):
    """Manda a notificação para todos os aparelhos inscritos de `usuarios`.

    `usuarios` é uma lista/queryset de `User`. `tag` faz o aparelho trocar a
    notificação anterior com a mesma tag em vez de empilhar outra.
    `exceto` tira quem fez a ação da lista.

    `em_segundo_plano=False` espera o envio terminar. É para os comandos
    agendados: a thread é daemon, e o processo do comando encerra logo depois
    do último aviso -- o que mataria o envio no meio.
    """
    if not push_configurado():
        return
    ids = {u.pk for u in usuarios if u is not None and u.is_active}
    if exceto is not None:
        ids.discard(exceto.pk)
    if not ids:
        return

    payload = json.dumps({'titulo': titulo, 'corpo': corpo, 'url': url, 'tag': tag})

    def disparar():
        inscricoes = list(
            InscricaoPush.objects.filter(user_id__in=ids).values('id', 'endpoint', 'p256dh', 'auth')
        )
        if not inscricoes:
            return
        if settings.PUSH_EM_SEGUNDO_PLANO and em_segundo_plano:
            threading.Thread(target=_enviar_em_thread, args=(inscricoes, payload), daemon=True).start()
        else:
            _enviar(inscricoes, payload)

    transaction.on_commit(disparar)


def _enviar_em_thread(inscricoes, payload):
    try:
        _enviar(inscricoes, payload)
    finally:
        # A thread abriu a própria conexão com o banco (para apagar as
        # inscrições vencidas); sem fechar, ela fica pendurada no MySQL.
        connections.close_all()


def _enviar(inscricoes, payload):
    vencidas = []
    for inscricao in inscricoes:
        try:
            webpush(
                subscription_info={
                    'endpoint': inscricao['endpoint'],
                    'keys': {'p256dh': inscricao['p256dh'], 'auth': inscricao['auth']},
                },
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                # Dicionário novo a cada envio: o pywebpush grava o `aud` do
                # endpoint nele, e reaproveitar mandaria o `aud` do aparelho
                # anterior (o serviço de push recusa).
                vapid_claims={'sub': settings.VAPID_CONTATO},
                ttl=VALIDADE_SEGUNDOS,
                timeout=10,
            )
        except WebPushException as erro:
            status = getattr(erro.response, 'status_code', None)
            if status in (404, 410):
                # O aparelho cancelou a inscrição (desinstalou o app, limpou os
                # dados, revogou a permissão): não adianta tentar de novo.
                vencidas.append(inscricao['id'])
            else:
                logger.warning('Falha ao enviar push (status %s): %s', status, erro)
        except Exception:
            logger.exception('Erro inesperado ao enviar push')
    if vencidas:
        InscricaoPush.objects.filter(id__in=vencidas).delete()


# ---------- Destinatários ----------

def _user_de(funcionario):
    usuario = getattr(funcionario, 'usuario', None)
    return usuario.user if usuario is not None else None


def _user_do_funcionario(funcionario):
    user = _user_de(funcionario)
    return [user] if user is not None else []


def _usuarios_rh_dp():
    return User.objects.filter(
        Q(usuario__papel__in=('rh', 'dp')) | Q(usuario__acesso_rh=True), is_active=True,
    )


def _usuarios_seguranca_rh():
    # Mesma regra de `Usuario.eh_seguranca_ou_rh`: quem valida ficha de EPI.
    return User.objects.filter(
        Q(usuario__papel__in=('seguranca_trabalho', 'rh')) | Q(usuario__acesso_rh=True), is_active=True,
    )


def _gestores_do_setor(setor_id):
    # Mesmo filtro da fila do gestor (`gestor_listar_ferias_view`): papel de
    # gestor no mesmo setor -- ser `Setor.gestor` não dá acesso de gestor.
    if setor_id is None:
        return User.objects.none()
    return User.objects.filter(
        usuario__papel='gestor', usuario__funcionario__setor_id=setor_id, is_active=True,
    )


def _gestores_da_equipe(funcionario):
    # Mesma regra do "Ponto da equipe" (`Usuario.equipe`): gestor do mesmo
    # setor ou o responsável pelo setor -- os dois enxergam e corrigem o ponto
    # dessa pessoa. Ser `Setor.gestor` sem papel de gestor não conta.
    if funcionario.setor_id is None:
        return User.objects.none()
    return User.objects.filter(
        Q(usuario__funcionario__setor_id=funcionario.setor_id)
        | Q(usuario__funcionario__setores_gerenciados=funcionario.setor_id),
        usuario__papel='gestor', is_active=True,
    ).distinct()


def _periodo(data, dias):
    return f'{dias} dias a partir de {data:%d/%m/%Y}'


# ---------- Férias ----------

def ferias_aguardando_gestor(solicitacao):
    funcionario = solicitacao.funcionario
    notificar(
        _gestores_do_setor(funcionario.setor_id),
        'Nova solicitação de férias',
        f'{funcionario.nome} pediu {_periodo(solicitacao.data_solicitada, solicitacao.dias_solicitados)}.',
        reverse('gestor_listar_ferias'),
        tag=f'ferias-{solicitacao.id}',
        exceto=_user_de(funcionario),
    )


def ferias_aguardando_rh(solicitacao):
    funcionario = solicitacao.funcionario
    notificar(
        _usuarios_rh_dp(),
        'Férias aguardando o RH',
        f'{funcionario.nome}: {_periodo(solicitacao.data_solicitada, solicitacao.dias_solicitados)}.',
        reverse('concluir_ferias'),
        tag=f'ferias-{solicitacao.id}',
        exceto=_user_de(funcionario),
    )


def ferias_aprovadas_pelo_gestor(solicitacao):
    notificar(
        _user_do_funcionario(solicitacao.funcionario),
        'Seu gestor aprovou suas férias',
        f'{_periodo(solicitacao.data_solicitada, solicitacao.dias_solicitados).capitalize()}. '
        'Agora falta a confirmação do RH.',
        reverse('solicitar_ferias'),
        tag=f'ferias-{solicitacao.id}',
    )


def ferias_sugestao_gestor(solicitacao):
    notificar(
        _user_do_funcionario(solicitacao.funcionario),
        'Seu gestor sugeriu outra data de férias',
        f'Sugestão: {_periodo(solicitacao.data_sugerida, solicitacao.dias_sugeridos)}. '
        'Abra para aceitar ou propor outra data.',
        reverse('solicitar_ferias'),
        tag=f'ferias-{solicitacao.id}',
    )


def ferias_confirmadas(solicitacao):
    notificar(
        _user_do_funcionario(solicitacao.funcionario),
        'Férias confirmadas',
        f'O RH confirmou suas férias: {_periodo(solicitacao.data_solicitada, solicitacao.dias_solicitados)}.',
        reverse('solicitar_ferias'),
        tag=f'ferias-{solicitacao.id}',
    )


def aviso_ferias_criado(aviso):
    funcionario = aviso.funcionario
    notificar(
        _user_do_funcionario(funcionario),
        'Hora de agendar suas férias',
        f'O RH pede que você solicite suas férias, com início previsto em {aviso.data_prevista:%d/%m/%Y}.',
        reverse('solicitar_ferias'),
        tag=f'aviso-ferias-{aviso.id}',
    )
    gestor = funcionario.setor.gestor if funcionario.setor_id else None
    gestor_usuario = getattr(gestor, 'usuario', None) if gestor else None
    if gestor_usuario is not None and gestor_usuario.papel == 'gestor':
        notificar(
            [gestor_usuario.user],
            'Férias a vencer na sua equipe',
            f'O RH avisou {funcionario.nome} para agendar as férias (previsão: {aviso.data_prevista:%d/%m/%Y}).',
            reverse('concluir_ferias'),
            tag=f'aviso-ferias-{aviso.id}',
        )


# ---------- Documentos, contrato e comodato ----------

def documento_rejeitado(documento):
    motivo = f' Motivo: {documento.motivo_rejeicao}' if documento.motivo_rejeicao else ''
    notificar(
        _user_do_funcionario(documento.funcionario),
        'Documento precisa ser reenviado',
        f'O RH recusou o documento "{documento.tipo_documento}".{motivo}',
        reverse('meus_documentos'),
        tag=f'documento-{documento.id}',
    )


def contrato_para_assinar(funcionario):
    notificar(
        _user_do_funcionario(funcionario),
        'Contrato de trabalho para assinar',
        'Seu contrato está pronto. Assine pelo gov.br e envie o arquivo assinado.',
        reverse('minhas_assinaturas'),
        tag=f'contrato-{funcionario.id}',
    )


def contrato_assinado_enviado(documento):
    funcionario = documento.funcionario
    notificar(
        _usuarios_rh_dp(),
        'Contrato assinado recebido',
        f'{funcionario.nome} enviou o contrato de trabalho assinado. Falta validar.',
        reverse('detalhe_funcionario', args=[funcionario.id]),
        tag=f'contrato-assinado-{funcionario.id}',
        exceto=_user_de(funcionario),
    )


def comodato_assinado_enviado(comodato):
    funcionario = comodato.funcionario
    notificar(
        _usuarios_rh_dp(),
        'Termo de comodato assinado recebido',
        f'{funcionario.nome} enviou o termo de {comodato.get_modalidade_display().lower()} assinado. Falta validar.',
        reverse('listar_comodatos') + '?pendentes=1',
        tag=f'comodato-assinado-{comodato.id}',
        exceto=_user_de(funcionario),
    )


def ficha_epi_assinada_enviada(ficha):
    funcionario = ficha.funcionario
    notificar(
        _usuarios_seguranca_rh(),
        'Ficha de EPI assinada recebida',
        f'{funcionario.nome} enviou a ficha de EPI assinada. Falta validar.',
        reverse('listar_fichas_epi'),
        tag=f'epi-assinada-{ficha.id}',
        exceto=_user_de(funcionario),
    )


def comodato_para_assinar(comodato):
    notificar(
        _user_do_funcionario(comodato.funcionario),
        'Termo de comodato para assinar',
        f'Há um termo de {comodato.get_modalidade_display().lower()} esperando sua assinatura.',
        reverse('meus_documentos'),
        tag=f'comodato-{comodato.id}',
    )


def comodato_rejeitado(comodato):
    motivo = f' Motivo: {comodato.motivo_rejeicao}' if comodato.motivo_rejeicao else ''
    notificar(
        _user_do_funcionario(comodato.funcionario),
        'Termo de comodato recusado',
        f'Assine e envie o termo de {comodato.get_modalidade_display().lower()} de novo.{motivo}',
        reverse('meus_documentos'),
        tag=f'comodato-{comodato.id}',
    )


# ---------- Ponto ----------

def ajuste_ponto_solicitado(ajuste):
    # Quem pode aprovar: o RH/DP e o gestor da equipe (a fila dele só mostra a equipe).
    funcionario = ajuste.funcionario
    rh = list(_usuarios_rh_dp())
    ids_rh = {user.pk for user in rh}
    notificar(
        rh + [user for user in _gestores_da_equipe(funcionario) if user.pk not in ids_rh],
        'Novo pedido de ajuste de ponto',
        f'{funcionario.nome}: {ajuste.get_tipo_evento_display().lower()} às '
        f'{ajuste.horario_ajustado:%H:%M} em {ajuste.data:%d/%m/%Y}.',
        reverse('ponto:ajustes_pendentes'),
        tag='ajustes-ponto',
        exceto=_user_de(funcionario),
    )


def ajuste_ponto_analisado(ajuste):
    dia = f'{ajuste.get_tipo_evento_display().lower()} de {ajuste.data:%d/%m/%Y}'
    if ajuste.status == 'aprovado':
        titulo, corpo = 'Ajuste de ponto aprovado', f'Seu pedido de {dia} foi aprovado.'
    else:
        motivo = f' Motivo: {ajuste.observacao_rh}' if ajuste.observacao_rh else ''
        titulo, corpo = 'Ajuste de ponto recusado', f'Seu pedido de {dia} foi recusado.{motivo}'
    notificar(
        _user_do_funcionario(ajuste.funcionario),
        titulo,
        corpo,
        reverse('ponto:meus_ajustes'),
        tag=f'ajuste-{ajuste.id}',
    )


def intervalo_curto(funcionario, data, saida, volta, minimo_segundos=60 * 60, em_segundo_plano=True):
    """
    Intervalo abaixo do mínimo -- 1h ou 15 min, conforme as horas trabalhadas
    (regra em `ponto.calculos.intervalo_minimo`): um aviso por caso para o RH/DP e
    para o gestor da equipe. Cada um abre a tela onde pode conferir o dia -- o RH
    o espelho, o gestor o ponto da equipe.
    """
    saida, volta = timezone.localtime(saida), timezone.localtime(volta)
    minutos = int((volta - saida).total_seconds() // 60)
    if minimo_segundos >= 60 * 60:
        titulo = 'Intervalo de almoço abaixo de 1h'
    else:
        titulo = f'Intervalo abaixo de {minimo_segundos // 60} min'
    corpo = f'{funcionario.nome} tirou {minutos} min de intervalo em {data:%d/%m} ({saida:%H:%M} às {volta:%H:%M}).'
    tag = f'intervalo-{funcionario.id}-{data:%Y%m%d}'
    periodo = f'data_inicio={data.isoformat()}&data_fim={data.isoformat()}'

    rh = list(_usuarios_rh_dp())
    notificar(
        rh, titulo, corpo,
        reverse('ponto:listar_calculo') + f'?funcionario={funcionario.id}&{periodo}',
        tag=tag, exceto=_user_de(funcionario), em_segundo_plano=em_segundo_plano,
    )
    # Quem é RH e gestor ao mesmo tempo já recebeu acima.
    ids_rh = {user.pk for user in rh}
    notificar(
        [user for user in _gestores_da_equipe(funcionario) if user.pk not in ids_rh], titulo, corpo,
        reverse('ponto:ponto_equipe_funcionario', args=[funcionario.id]) + f'?{periodo}',
        tag=tag, exceto=_user_de(funcionario), em_segundo_plano=em_segundo_plano,
    )
