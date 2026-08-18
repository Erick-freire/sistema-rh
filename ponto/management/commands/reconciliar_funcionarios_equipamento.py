import re
import unicodedata

import pymssql
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from funcionarios.models import Funcionario
from ponto import controlid_client as client
from ponto.controlid_client import ControlIdAPIError
from ponto.models import Equipamento, SincronizacaoFuncionario
from ponto.services import _hash_dados_funcionario


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""
    pass


def _so_digitos(valor):
    if valor is None:
        return ''
    return re.sub(r'\D', '', str(valor))


def _normaliza_nome(valor):
    sem_acento = unicodedata.normalize('NFKD', valor or '').encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', sem_acento).strip().upper()


class Command(BaseCommand):
    help = (
        'Descobre quem é quem entre os funcionários já cadastrados diretamente nos equipamentos '
        'Control iD (cadastro feito pelo Secullum, antes deste sistema existir) e os funcionários '
        'do RH, e marca a sincronização como já feita -- pra não recadastrar (e duplicar sem '
        'biometria) quem já está lá. Casa por CPF, e quando o equipamento usa o PIS como chave '
        '(comum no relógio ADM) usa o próprio Secullum como ponte pra achar o nome da pessoa.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Só mostra o relatório, não grava nada.')

    def handle(self, *args, **options):
        if not settings.SECULLUM_SERVER:
            raise CommandError(
                'Configure SECULLUM_SERVER/SECULLUM_INSTANCE/SECULLUM_DATABASE/SECULLUM_USER/'
                'SECULLUM_PASSWORD no .env antes de rodar.'
            )

        dry_run = options['dry_run']

        servidor = settings.SECULLUM_SERVER
        if settings.SECULLUM_INSTANCE:
            servidor = f'{servidor}\\{settings.SECULLUM_INSTANCE}'

        conexao = pymssql.connect(
            server=servidor,
            user=settings.SECULLUM_USER,
            password=settings.SECULLUM_PASSWORD,
            database=settings.SECULLUM_DATABASE,
            timeout=30,
            login_timeout=10,
            as_dict=True,
        )
        try:
            cursor = conexao.cursor()
            cursor.execute('SELECT nome, cpf, n_pis FROM funcionarios')
            secullum_por_identificador = {}
            for linha in cursor.fetchall():
                for campo in (linha['cpf'], linha['n_pis']):
                    identificador = _so_digitos(campo)
                    if identificador:
                        secullum_por_identificador.setdefault(identificador, []).append(linha['nome'])
        finally:
            conexao.close()

        funcionarios_por_cpf = {_so_digitos(f.cpf): f for f in Funcionario.objects.all()}
        funcionarios_por_nome = {}
        for funcionario in Funcionario.objects.all():
            funcionarios_por_nome.setdefault(_normaliza_nome(funcionario.nome), []).append(funcionario)

        pronto_para_gravar = []  # (funcionario, equipamento, identificador, tem_biometria)
        sem_match_total = 0
        equipamentos_com_erro = []
        funcionarios_vistos = set()

        for equipamento in Equipamento.objects.filter(ativo=True):
            try:
                session = client.login(equipamento)
                usuarios = []
                offset = 0
                while True:
                    pagina = client.load_users(equipamento, session, limit=100, offset=offset)
                    if not pagina:
                        break
                    usuarios.extend(pagina)
                    offset += len(pagina)
                    if len(pagina) < 100:
                        break
            except ControlIdAPIError as erro:
                equipamentos_com_erro.append((equipamento, str(erro)))
                continue

            for usuario in usuarios:
                identificador = _so_digitos(usuario.get('cpf') or usuario.get('pis'))

                funcionario = funcionarios_por_cpf.get(identificador)

                if not funcionario:
                    for nome_secullum in secullum_por_identificador.get(identificador, []):
                        candidatos = funcionarios_por_nome.get(_normaliza_nome(nome_secullum))
                        if candidatos and len(candidatos) == 1:
                            funcionario = candidatos[0]
                            break

                if not funcionario:
                    candidatos = funcionarios_por_nome.get(_normaliza_nome(usuario.get('name', '')))
                    if candidatos and len(candidatos) == 1:
                        funcionario = candidatos[0]

                if not funcionario:
                    sem_match_total += 1
                    continue

                funcionarios_vistos.add(funcionario.id)
                pronto_para_gravar.append((funcionario, equipamento, identificador, bool(usuario.get('templates_count'))))

        try:
            with transaction.atomic():
                criadas = atualizadas = ignoradas = 0
                if not dry_run:
                    for funcionario, equipamento, identificador, tem_biometria in pronto_para_gravar:
                        sincronizacao, criada = SincronizacaoFuncionario.objects.get_or_create(
                            funcionario=funcionario, equipamento=equipamento,
                            defaults={'hash_dados_sincronizados': ''},
                        )
                        if not criada and sincronizacao.status == 'sincronizado':
                            ignoradas += 1
                            continue

                        sincronizacao.acao_pendente = 'cadastrar'
                        sincronizacao.id_usuario_dispositivo = identificador
                        sincronizacao.status = 'sincronizado' if tem_biometria else 'aguardando_biometria'
                        sincronizacao.hash_dados_sincronizados = _hash_dados_funcionario(funcionario)
                        sincronizacao.tentativas = 0
                        sincronizacao.ultimo_erro = ''
                        sincronizacao.save()
                        criadas += 1 if criada else 0
                        atualizadas += 0 if criada else 1

                self.stdout.write(self.style.SUCCESS(
                    f'{len(pronto_para_gravar)} vínculo(s) funcionário-equipamento identificado(s) '
                    f'({criadas} sincronização(ões) nova(s), {atualizadas} atualizada(s), {ignoradas} já estavam ok).'
                ))
                self.stdout.write(f'{len(funcionarios_vistos)} funcionário(s) do RH encontrados em pelo menos um equipamento.')
                nao_vistos = Funcionario.objects.exclude(id__in=funcionarios_vistos).count()
                if nao_vistos:
                    self.stdout.write(self.style.WARNING(
                        f'{nao_vistos} funcionário(s) do RH não aparecem em nenhum equipamento -- '
                        'seguem pela sincronização normal (cadastro novo).'
                    ))
                if sem_match_total:
                    self.stdout.write(
                        f'{sem_match_total} cadastro(s) nos equipamentos sem correspondência no RH '
                        '(provavelmente desligados antigos, gente ainda não migrada pro RH novo, ou cadastro de teste).'
                    )
                for equipamento, erro in equipamentos_com_erro:
                    self.stdout.write(self.style.WARNING(f'Não consegui conectar em "{equipamento.nome}": {erro}'))

                if dry_run:
                    self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()
        except DryRunRollback:
            pass
