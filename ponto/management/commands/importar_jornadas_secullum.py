import re
import unicodedata

import pymssql
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from funcionarios.models import Funcionario
from ponto.models import FuncionarioJornada, Horario


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""
    pass


def _so_digitos(valor):
    return re.sub(r'\D', '', valor or '')


def _normaliza_nome(valor):
    sem_acento = unicodedata.normalize('NFKD', valor or '').encode('ascii', 'ignore').decode('ascii')
    return re.sub(r'\s+', ' ', sem_acento).strip().upper()


class Command(BaseCommand):
    help = (
        'Vincula o horário de cada funcionário do RH ao horário correspondente já cadastrado aqui '
        '(não importa horário novo -- só liga quem já existe), lendo o `horario_num`/`escala_id` de '
        'cada um no Secullum Ponto 4 (somente leitura lá). Casa por CPF e, se não achar, por nome '
        'normalizado. Nunca sobrescreve quem já tem uma jornada definida no RH.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Só mostra o que faria, não grava nada.')

    def handle(self, *args, **options):
        if not settings.SECULLUM_SERVER:
            raise CommandError(
                'Configure SECULLUM_SERVER/SECULLUM_INSTANCE/SECULLUM_DATABASE/SECULLUM_USER/'
                'SECULLUM_PASSWORD no .env antes de importar.'
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

            cursor.execute('SELECT nome, cpf, horario_num, escala_id, admissao, demissao FROM funcionarios')
            funcionarios_secullum = cursor.fetchall()

            cursor.execute('SELECT id, tamanho, data_base FROM escalas')
            escalas = {linha['id']: linha for linha in cursor.fetchall()}

            cursor.execute('SELECT escala_id, ciclo, horario_num FROM escalas_itens ORDER BY escala_id, ciclo')
            itens_por_escala = {}
            for linha in cursor.fetchall():
                itens_por_escala.setdefault(linha['escala_id'], []).append(linha)

            cursor.execute("SELECT numero FROM horarios WHERE nome LIKE '%FOLGA%'")
            numeros_folga = {linha['numero'] for linha in cursor.fetchall()}
        finally:
            conexao.close()

        por_cpf = {}
        por_nome = {}
        for linha in funcionarios_secullum:
            cpf = _so_digitos(linha['cpf'])
            if cpf:
                por_cpf.setdefault(cpf, []).append(linha)
            por_nome.setdefault(_normaliza_nome(linha['nome']), []).append(linha)

        numeros_confiaveis = set(Horario.objects.values_list('numero', flat=True))
        ja_configurados = set(FuncionarioJornada.objects.values_list('funcionario_id', flat=True))

        prontos = []
        horario_nao_confiavel = []
        ciclo_nao_suportado = []
        ambiguo = []
        sem_match = []

        for funcionario in Funcionario.objects.exclude(id__in=ja_configurados):
            candidatos = por_cpf.get(_so_digitos(funcionario.cpf)) or por_nome.get(_normaliza_nome(funcionario.nome))
            if not candidatos:
                sem_match.append(funcionario)
                continue

            if len(candidatos) > 1:
                ativos = [c for c in candidatos if c['demissao'] is None]
                if len(ativos) == 1:
                    candidatos = ativos
                else:
                    ambiguo.append((funcionario, candidatos))
                    continue

            secullum = candidatos[0]
            horario_num = secullum['horario_num']
            paridade_ciclo = None

            if secullum['escala_id']:
                escala = escalas.get(secullum['escala_id'])
                itens = itens_por_escala.get(secullum['escala_id'], [])
                if not escala or escala['tamanho'] != 2 or len(itens) != 2:
                    ciclo_nao_suportado.append((funcionario, secullum, escala))
                    continue

                trabalho = [i for i in itens if i['horario_num'] and i['horario_num'] not in numeros_folga]
                if len(trabalho) != 1:
                    ciclo_nao_suportado.append((funcionario, secullum, escala))
                    continue

                item_trabalho = trabalho[0]
                horario_num = item_trabalho['horario_num']
                paridade_data_base = 'par' if escala['data_base'].day % 2 == 0 else 'impar'
                paridade_ciclo = paridade_data_base if item_trabalho['ciclo'] == 1 else (
                    'impar' if paridade_data_base == 'par' else 'par'
                )

            if horario_num not in numeros_confiaveis:
                horario_nao_confiavel.append((funcionario, horario_num))
                continue

            data_inicio = funcionario.data_admissao or (secullum['admissao'].date() if secullum['admissao'] else None)
            prontos.append((funcionario, horario_num, paridade_ciclo, data_inicio))

        try:
            with transaction.atomic():
                criados = 0
                if not dry_run:
                    for funcionario, horario_num, paridade_ciclo, data_inicio in prontos:
                        horario = Horario.objects.get(numero=horario_num)
                        FuncionarioJornada.objects.create(
                            funcionario=funcionario,
                            horario=horario,
                            paridade_ciclo=paridade_ciclo,
                            data_inicio=data_inicio or timezone.now().date(),
                        )
                        criados += 1

                self.stdout.write(self.style.SUCCESS(
                    f'{len(prontos)} funcionário(s) {"prontos pra vincular" if dry_run else "vinculados"} '
                    f'({criados} gravado(s)).'
                ))
                if ja_configurados:
                    self.stdout.write(f'{len(ja_configurados)} já tinham jornada definida -- não mexido.')
                if sem_match:
                    self.stdout.write(self.style.WARNING(f'{len(sem_match)} sem correspondência no Secullum (nem por CPF, nem por nome):'))
                    for f in sem_match:
                        self.stdout.write(f'  - {f.nome} (CPF {f.cpf})')
                if horario_nao_confiavel:
                    self.stdout.write(self.style.WARNING(f'{len(horario_nao_confiavel)} com horário fora do nosso catálogo (não importado, confira manualmente):'))
                    for f, num in horario_nao_confiavel:
                        self.stdout.write(f'  - {f.nome}: Secullum aponta horário {num}, que não existe no nosso sistema')
                if ciclo_nao_suportado:
                    self.stdout.write(self.style.WARNING(f'{len(ciclo_nao_suportado)} numa escala de ciclo != 2 dias (ainda não suportado, atribua manualmente):'))
                    for f, secullum, escala in ciclo_nao_suportado:
                        self.stdout.write(f'  - {f.nome}: escala_id {secullum["escala_id"]} (ciclo de {escala["tamanho"] if escala else "?"} dias)')
                if ambiguo:
                    self.stdout.write(self.style.WARNING(f'{len(ambiguo)} com mais de uma correspondência possível no Secullum (confira manualmente):'))
                    for f, candidatos in ambiguo:
                        self.stdout.write(f'  - {f.nome} (CPF {f.cpf})')

                if dry_run:
                    self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()
        except DryRunRollback:
            pass
