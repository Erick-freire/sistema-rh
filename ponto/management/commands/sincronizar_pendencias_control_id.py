from django.core.management.base import BaseCommand

from ponto.services import processar_pendencias


class Command(BaseCommand):
    help = (
        'Processa as pendências de sincronização de funcionários com os equipamentos Control iD '
        '(retry automático). Pensado para rodar periodicamente via agendador de tarefas.'
    )

    def handle(self, *args, **options):
        processadas, sucesso = processar_pendencias()
        self.stdout.write(self.style.SUCCESS(
            f'{processadas} pendência(s) processada(s), {sucesso} com sucesso, '
            f'{processadas - sucesso} com erro (ficam para o próximo retry).'
        ))
