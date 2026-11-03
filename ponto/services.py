import hashlib
import re
from datetime import datetime, time, timedelta

from django.db import close_old_connections, connection, transaction
from django.db.models import Count, Max, Min, Q
from django.utils import timezone

from . import controlid_client as client
from .controlid_client import ControlIdAPIError, ControlIdTimeoutError, ControlIdUsuarioNaoEncontradoError
from .models import Equipamento, MarcacaoRelogio, SincronizacaoFuncionario

# Acima disso, um erro que continua repetindo (ex.: relógio configurado por
# PIS, mas só temos o CPF de quem é novo) provavelmente não é passageiro --
# retentar sem limite em toda sincronização só desperdiça tempo tentando de
# novo dezenas ou centenas de pendências que nunca vão vingar sozinhas.
LIMITE_TENTATIVAS_AUTOMATICAS = 5


def _hash_dados_funcionario(funcionario):
    """
    Hash dos campos que realmente são enviados ao equipamento. Usado para não
    reenfileirar sincronização quando o `Funcionario` é salvo por causa de um
    campo que a Control iD nem armazena (foto, endereço, etc.).
    """
    dados = f'{funcionario.nome}|{funcionario.cpf}|{funcionario.status}'
    return hashlib.sha256(dados.encode()).hexdigest()


def enfileirar_sincronizacao(funcionario):
    """
    Chamado pelo signal post_save de Funcionario. Só grava estado local
    (rápido, sem chamada de rede) — quem fala com o equipamento de fato é
    sempre `processar_pendencias` ou uma sincronização manual.
    """
    hash_atual = _hash_dados_funcionario(funcionario)
    acao = 'desativar' if funcionario.status == 'inativo' else 'cadastrar'

    for equipamento in Equipamento.objects.filter(ativo=True):
        sincronizacao, criada = SincronizacaoFuncionario.objects.get_or_create(
            funcionario=funcionario,
            equipamento=equipamento,
            defaults={'hash_dados_sincronizados': '', 'acao_pendente': acao},
        )

        precisa_atualizar = (
            criada
            or sincronizacao.acao_pendente != acao
            or (acao == 'cadastrar' and sincronizacao.hash_dados_sincronizados != hash_atual)
        )

        if precisa_atualizar:
            sincronizacao.acao_pendente = acao
            sincronizacao.status = 'pendente'
            sincronizacao.save(update_fields=['acao_pendente', 'status', 'atualizado_em'])


def testar_conexao(equipamento):
    """
    Login de teste no equipamento (+ get_about, para conferir o número de
    série contra o que já está cadastrado). Atualiza status_conexao/
    ultima_comunicacao.
    """
    try:
        session = client.login(equipamento)
        info = client.get_about(equipamento, session)
    except ControlIdAPIError as erro:
        equipamento.status_conexao = 'offline'
        equipamento.ultimo_erro_comunicacao = str(erro)
        equipamento.save(update_fields=['status_conexao', 'ultimo_erro_comunicacao', 'atualizado_em'])
        return False, str(erro)

    equipamento.status_conexao = 'online'
    equipamento.ultima_comunicacao = timezone.now()
    equipamento.ultimo_erro_comunicacao = ''
    if info.get('nSerie') and not equipamento.numero_serie:
        equipamento.numero_serie = info['nSerie']
    equipamento.save(update_fields=[
        'status_conexao', 'ultima_comunicacao', 'ultimo_erro_comunicacao', 'numero_serie', 'atualizado_em',
    ])
    return True, None


def _cadastrar_ou_atualizar(equipamento, session, sincronizacao):
    """
    Usa o identificador já registrado no equipamento (`id_usuario_dispositivo`)
    quando ele existe -- alguns cadastros vieram do Secullum usando o PIS em
    vez do CPF como chave (ex.: equipamento ADM); se atualizássemos sempre
    pelo CPF do funcionário, um PIS-cadastrado receberia "usuário não
    encontrado", cairia no add_user, e duplicaria o cadastro dele sem a
    biometria já capturada. Só usa o CPF quando ainda não sabemos o
    identificador de verdade (primeiro cadastro).
    """
    funcionario = sincronizacao.funcionario
    identificador = sincronizacao.id_usuario_dispositivo or funcionario.cpf

    try:
        client.update_user(equipamento, session, identificador, funcionario.nome)
    except ControlIdUsuarioNaoEncontradoError:
        identificador = funcionario.cpf
        client.add_user(equipamento, session, identificador, funcionario.nome)

    usuario_no_equipamento = client.buscar_usuario_por_cpf(equipamento, session, identificador)
    tem_biometria = bool(usuario_no_equipamento and usuario_no_equipamento.get('templates_count'))

    sincronizacao.id_usuario_dispositivo = identificador
    sincronizacao.status = 'sincronizado' if tem_biometria else 'aguardando_biometria'
    sincronizacao.hash_dados_sincronizados = _hash_dados_funcionario(funcionario)


def _desativar(equipamento, session, sincronizacao):
    """
    Remove o funcionário desligado do cadastro do equipamento (bloqueia novas
    marcações por biometria/cartão) sem tocar no histórico já coletado --
    MarcacaoRelogio tem FK protegida, não é afetada por isto.

    Usa `id_usuario_dispositivo` (pode ser PIS, não só CPF -- ver
    `_cadastrar_ou_atualizar`) quando já conhecido, senão tenta pelo CPF.

    NOTA: equipamentos REP homologados pela Portaria 671 costumam ser
    obrigados a aceitar marcação manual mesmo sem cadastro -- confirmar esse
    detalhe legal antes de depender só disto para "bloquear" o funcionário.
    """
    identificador = sincronizacao.id_usuario_dispositivo or sincronizacao.funcionario.cpf
    try:
        client.remove_user(equipamento, session, identificador)
    except ControlIdUsuarioNaoEncontradoError:
        pass  # já não existia no equipamento -- objetivo alcançado de qualquer forma

    sincronizacao.status = 'sincronizado'


def sincronizar_funcionario_no_equipamento(sincronizacao):
    equipamento = sincronizacao.equipamento

    try:
        session = client.login(equipamento)

        if sincronizacao.acao_pendente == 'desativar':
            _desativar(equipamento, session, sincronizacao)
        else:
            _cadastrar_ou_atualizar(equipamento, session, sincronizacao)

        sincronizacao.ultimo_erro = ''
        sincronizacao.save()

        equipamento.status_conexao = 'online'
        equipamento.ultima_comunicacao = timezone.now()
        equipamento.save(update_fields=['status_conexao', 'ultima_comunicacao', 'atualizado_em'])
        return True

    except ControlIdAPIError as erro:
        sincronizacao.status = 'erro'
        sincronizacao.tentativas += 1
        sincronizacao.ultimo_erro = str(erro)
        sincronizacao.save()

        if isinstance(erro, ControlIdTimeoutError):
            equipamento.status_conexao = 'offline'
            equipamento.ultimo_erro_comunicacao = str(erro)
            equipamento.save(update_fields=['status_conexao', 'ultimo_erro_comunicacao', 'atualizado_em'])

        return False


def processar_pendencias(equipamento=None):
    """
    Roda o retry automático -- de todos os equipamentos, ou só de um
    (sincronização manual "por equipamento", igual ao Enviar/Receber do
    Secullum). `select_for_update(skip_locked=True)` garante que dois
    processos rodando ao mesmo tempo nunca peguem a mesma pendência.
    """
    processadas = 0
    sucesso = 0

    pendencias = SincronizacaoFuncionario.objects.filter(
        Q(status='pendente') | Q(status='erro', tentativas__lt=LIMITE_TENTATIVAS_AUTOMATICAS)
    )
    if equipamento is not None:
        pendencias = pendencias.filter(equipamento=equipamento)

    ids_pendentes = list(pendencias.values_list('id', flat=True))

    for sincronizacao_id in ids_pendentes:
        # Cada pendência pode envolver uma chamada lenta a um equipamento --
        # numa execução via management command (sem o ciclo de
        # request/response do Django cuidando disso sozinho), a conexão com o
        # banco pode ficar tempo demais ociosa e cair ("MySQL server has gone
        # away") antes da próxima iteração. `in_atomic_block` evita mexer na
        # conexão quando já estamos dentro de uma transação (ex.: os testes,
        # que envolvem cada teste numa transação própria para dar rollback).
        if not connection.in_atomic_block:
            close_old_connections()
        with transaction.atomic():
            sincronizacao = (
                SincronizacaoFuncionario.objects
                .select_for_update(skip_locked=True)
                .filter(id=sincronizacao_id)
                .first()
            )
            if sincronizacao is None:
                continue

            processadas += 1
            if sincronizar_funcionario_no_equipamento(sincronizacao):
                sucesso += 1

    return processadas, sucesso


TAMANHO_LOTE_COLETA = 2000  # ver docstring de `coletar_marcacoes`


def _como_numero(valor):
    """Só os dígitos, como número: '070819163171' e '70819163171' são o mesmo identificador. None sem dígitos."""
    digitos = re.sub(r'\D', '', str(valor or ''))
    return int(digitos) if digitos else None


def funcionarios_por_identificador(equipamento):
    """
    Os dois mapas {identificador: funcionario_id} usados pra achar o dono das
    marcações deste equipamento (ver `funcionario_da_marcacao`): o identificador
    que o próprio equipamento usa pra pessoa (`id_usuario_dispositivo`, gravado
    pela reconciliação/sincronização) e o CPF/PIS do cadastro.

    O primeiro existe por causa do relógio ADM: pra quem não tem PIS, o Secullum
    cadastrou lá um "PIS" de 12 dígitos começando com 8, parecido com o CPF mas
    nem sempre igual (CPF 70819163171, no relógio 870819163173). Só com o CPF/PIS
    do cadastro as batidas dessas pessoas ficavam sem funcionário.
    """
    from funcionarios.models import Funcionario

    por_dispositivo = {}
    for funcionario_id, identificador in SincronizacaoFuncionario.objects.filter(
        equipamento=equipamento,
    ).values_list('funcionario_id', 'id_usuario_dispositivo'):
        numero = _como_numero(identificador)
        if numero:
            por_dispositivo[numero] = funcionario_id

    por_documento = {}
    for funcionario_id, cpf, pis, status in Funcionario.objects.values_list('id', 'cpf', 'pis', 'status'):
        for valor in (cpf, pis):
            numero = _como_numero(valor)
            # Mesmo CPF em dois cadastros (ex.: readmissão): fica o ativo.
            if numero and (numero not in por_documento or status == 'ativo'):
                por_documento[numero] = funcionario_id

    return por_dispositivo, por_documento


def funcionario_da_marcacao(registro, mapas):
    """
    funcionario_id do dono de um registro do AFD (de `parse_afd`, ou o
    `dados_brutos` já gravado), ou None. O AFD traz o identificador em 12
    posições: num CPF a primeira é um 0 de preenchimento, mas no "PIS" de 12
    dígitos do ADM ela faz parte do número -- por isso o número inteiro vem antes
    dos 11 últimos dígitos. O identificador do equipamento vem antes do CPF/PIS.
    """
    candidatos = [
        _como_numero((registro.get('prefixo') or '') + (registro.get('identificador') or '')),
        _como_numero(registro.get('identificador')),
    ]
    for mapa in mapas:
        for numero in candidatos:
            if numero and numero in mapa:
                return mapa[numero]
    return None


def inicio_da_revinculacao(hoje=None):
    """
    1º dia do mês anterior (aware, hora local): até onde a coleta volta pra
    revincular marcações sem funcionário. Cobre a competência aberta e a que
    está sendo fechada; mais pra trás, só com o comando
    `vincular_marcacoes_sem_funcionario`, que varre o histórico todo.
    """
    hoje = hoje or timezone.localdate()
    mes_anterior = (hoje.replace(day=1) - timedelta(days=1)).replace(day=1)
    return timezone.make_aware(datetime.combine(mes_anterior, time.min))


def vincular_marcacoes_sem_funcionario(equipamento, mapas=None, desde=None):
    """
    Dá dono às marcações já gravadas sem funcionário cujo identificador agora
    é conhecido, e devolve quantas vinculou. A coleta só grava o que é novo (e
    `ignore_conflicts` não mexe no que já existe), então uma marcação que
    entrou sem dono fica assim pra sempre se ninguém a revisitar -- o que
    acontece com quem bate ponto antes de ser cadastrado aqui, antes da
    reconciliação gravar o identificador do relógio, ou com marcação coletada
    por uma versão antiga da regra (caso da matrícula 1473 em 22/10/2026).
    `desde=None` varre o histórico todo.
    """
    if mapas is None:
        mapas = funcionarios_por_identificador(equipamento)

    marcacoes = MarcacaoRelogio.objects.filter(equipamento=equipamento, funcionario__isnull=True)
    if desde:
        marcacoes = marcacoes.filter(momento__gte=desde)

    vinculadas = []
    for marcacao in marcacoes.only('id', 'dados_brutos').order_by().iterator(chunk_size=2000):
        funcionario_id = funcionario_da_marcacao(marcacao.dados_brutos or {}, mapas)
        if funcionario_id:
            marcacao.funcionario_id = funcionario_id
            vinculadas.append(marcacao)

    MarcacaoRelogio.objects.bulk_update(vinculadas, ['funcionario_id'], batch_size=2000)
    return len(vinculadas)


def resumo_marcacoes_sem_funcionario(desde):
    """
    Quem bateu ponto desde `desde` sem ter dono no sistema, agrupado por
    equipamento e identificador: em geral alguém admitido no Secullum/relógio
    e ainda não cadastrado aqui, ou demitido que continua no relógio. Assim
    que o cadastro (ou o vínculo do relógio) existir, a próxima coleta vincula
    essas marcações sozinha.
    """
    linhas = (
        MarcacaoRelogio.objects
        .filter(funcionario__isnull=True, momento__gte=desde)
        .values('equipamento__nome', 'dados_brutos__prefixo', 'dados_brutos__identificador')
        .annotate(quantidade=Count('id'), primeira=Min('momento'), ultima=Max('momento'))
        .order_by('-quantidade')
    )
    return [
        {
            'equipamento': linha['equipamento__nome'],
            # Prefixo diferente de 0 faz parte do número (o "PIS" de 12 dígitos do ADM).
            'identificador': (
                (linha['dados_brutos__prefixo'] or '').lstrip('0') + (linha['dados_brutos__identificador'] or '')
            ),
            'quantidade': linha['quantidade'],
            'primeira': linha['primeira'],
            'ultima': linha['ultima'],
        }
        for linha in linhas
    ]


def coletar_marcacoes(equipamento):
    """
    Coleta incremental e idempotente: só pede eventos com NSR maior que o
    checkpoint salvo (get_afd é exclusivo no `initial_nsr`), e usa
    `ignore_conflicts` (constraint única por equipamento+NSR) para nunca
    duplicar uma marcação já gravada -- mesmo se o NSR de corte se repetir
    entre duas chamadas.

    O get_afd.fcgi não tem paginação -- devolve tudo desde `initial_nsr` numa
    resposta só, não tem como "pedir menos" ao equipamento. Em equipamentos
    com muito histórico acumulado (ex.: Farinha, 641 cadastros) isso vira uma
    resposta enorme, e processar/gravar tudo de uma vez só salvando o
    checkpoint no final tinha um problema sério: se a operação fosse
    interrompida no meio (timeout de proxy/navegador), o checkpoint nunca
    avançava e a tentativa seguinte recomeçava do zero -- travava pra sempre
    no mesmo ponto. Processa e salva o checkpoint em lotes: se for
    interrompida no meio, o que já foi processado fica salvo, e a próxima
    tentativa parte dali em vez de repetir tudo -- converge em algumas
    tentativas em vez de travar pra sempre.

    Antes de buscar as novas, revincula as recentes que ficaram sem dono (ver
    `vincular_marcacoes_sem_funcionario`) -- mesmo se o relógio estiver
    offline ou sem nada novo, já que o que muda é o nosso cadastro.
    """
    # O AFD não traz matrícula, só o identificador da pessoa no equipamento
    # (CPF, PIS, ou o "PIS" gerado do ADM) -- ver `funcionario_da_marcacao`.
    mapas = funcionarios_por_identificador(equipamento)
    vincular_marcacoes_sem_funcionario(equipamento, mapas, desde=inicio_da_revinculacao())

    try:
        session = client.login(equipamento)
        texto = client.get_afd(equipamento, session, equipamento.ultimo_nsr_coletado)
    except ControlIdAPIError as erro:
        equipamento.status_conexao = 'offline'
        equipamento.ultimo_erro_comunicacao = str(erro)
        equipamento.save(update_fields=['status_conexao', 'ultimo_erro_comunicacao', 'atualizado_em'])
        return 0

    registros = client.parse_afd(texto)

    if not registros:
        equipamento.status_conexao = 'online'
        equipamento.ultima_comunicacao = timezone.now()
        equipamento.save(update_fields=['status_conexao', 'ultima_comunicacao', 'atualizado_em'])
        return 0

    total_novas = 0
    for inicio in range(0, len(registros), TAMANHO_LOTE_COLETA):
        lote = registros[inicio:inicio + TAMANHO_LOTE_COLETA]
        marcacoes = [
            MarcacaoRelogio(
                equipamento=equipamento,
                funcionario_id=funcionario_da_marcacao(registro, mapas),
                id_evento_dispositivo=str(registro['nsr']),
                momento=registro['momento'],
                tipo_evento='desconhecido',
                dados_brutos={**registro, 'momento': registro['momento'].isoformat()},
            )
            for registro in lote
        ]

        # bulk_create com ignore_conflicts não informa quantas linhas foram de
        # fato inseridas (duplicatas batidas contra a constraint única são
        # silenciosamente puladas) -- contamos antes/depois do lote pra
        # reportar o número real de novidades.
        total_antes = MarcacaoRelogio.objects.filter(equipamento=equipamento).count()
        MarcacaoRelogio.objects.bulk_create(marcacoes, ignore_conflicts=True)
        total_depois = MarcacaoRelogio.objects.filter(equipamento=equipamento).count()
        total_novas += total_depois - total_antes

        equipamento.ultimo_nsr_coletado = max(r['nsr'] for r in lote)
        equipamento.status_conexao = 'online'
        equipamento.ultima_comunicacao = timezone.now()
        equipamento.save(update_fields=[
            'ultimo_nsr_coletado', 'status_conexao', 'ultima_comunicacao', 'atualizado_em',
        ])

        if not connection.in_atomic_block:
            close_old_connections()

    return total_novas
