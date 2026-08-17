import pymssql
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ponto.models import Horario, HorarioDia


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""
    pass


class Command(BaseCommand):
    help = (
        'Importa jornadas (tabela `horarios`) do Secullum Ponto 4 -- somente leitura lá -- para '
        'o módulo de Horários do RH. Não replica as ~100 colunas do Secullum, só os campos que '
        'realmente variam entre os horários da empresa (entrada/saída por dia da semana).'
    )

    def add_arguments(self, parser):
        parser.add_argument('numero_inicio', type=int)
        parser.add_argument('numero_fim', type=int)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        if not settings.SECULLUM_SERVER:
            raise CommandError(
                'Configure SECULLUM_SERVER/SECULLUM_INSTANCE/SECULLUM_DATABASE/SECULLUM_USER/'
                'SECULLUM_PASSWORD no .env antes de importar.'
            )

        numero_inicio = options['numero_inicio']
        numero_fim = options['numero_fim']
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
            cursor.execute("""
                SELECT numero, nome, dia_semana, entrada1, saida1, entrada2, saida2,
                       entrada3, saida3, considerar_feriados, separar_normais_not
                FROM horarios
                WHERE numero BETWEEN %s AND %s
                ORDER BY numero, dia_semana
            """, (numero_inicio, numero_fim))
            linhas = cursor.fetchall()
        finally:
            conexao.close()

        if not linhas:
            self.stdout.write(self.style.WARNING('Nenhum horário encontrado nesse intervalo no Secullum.'))
            return

        por_numero = {}
        for linha in linhas:
            por_numero.setdefault(linha['numero'], []).append(linha)

        horarios_criados = 0
        horarios_atualizados = 0

        try:
            with transaction.atomic():
                for numero, dias in por_numero.items():
                    cabecalho = dias[0]
                    horario, criado = Horario.objects.update_or_create(
                        numero=numero,
                        defaults={
                            'nome': cabecalho['nome'] or '',
                            'considerar_feriados': bool(cabecalho['considerar_feriados']),
                            'separar_normais_noturnas': bool(cabecalho['separar_normais_not']),
                        },
                    )
                    horarios_criados += 1 if criado else 0
                    horarios_atualizados += 0 if criado else 1

                    for dia in dias:
                        HorarioDia.objects.update_or_create(
                            horario=horario,
                            dia_semana=dia['dia_semana'],
                            defaults={
                                'entrada1': dia['entrada1'] or None,
                                'saida1': dia['saida1'] or None,
                                'entrada2': dia['entrada2'] or None,
                                'saida2': dia['saida2'] or None,
                                'entrada3': dia['entrada3'] or None,
                                'saida3': dia['saida3'] or None,
                            },
                        )

                self.stdout.write(self.style.SUCCESS(
                    f'{len(por_numero)} horário(s) processado(s) '
                    f'({horarios_criados} criado(s), {horarios_atualizados} atualizado(s)).'
                ))

                if dry_run:
                    self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()
        except DryRunRollback:
            pass
