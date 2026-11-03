from django.core.management.base import BaseCommand
from django.db import transaction

from ponto.models import Equipamento
from ponto.services import vincular_marcacoes_sem_funcionario


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""
    pass


class Command(BaseCommand):
    help = (
        'Revincula marcações já coletadas que ficaram sem funcionário (identificador do equipamento não '
        'batia com ninguém conhecido na hora da coleta) -- útil depois de importar o PIS de mais gente '
        '(ver importar_pis_planilha), de cadastrar quem já batia ponto ou de rodar '
        'reconciliar_funcionarios_equipamento, sem precisar coletar tudo de novo do equipamento. '
        'A coleta já faz isso sozinha desde o 1º dia do mês anterior; este comando varre o histórico todo. '
        'Usa a mesma regra da coleta (ver ponto.services.funcionario_da_marcacao).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--equipamento', type=str, help='Nome do equipamento (padrão: todos).')
        parser.add_argument('--dry-run', action='store_true', help='Só mostra o relatório, não grava nada.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        equipamentos = Equipamento.objects.all()
        if options['equipamento']:
            equipamentos = equipamentos.filter(nome=options['equipamento'])

        try:
            with transaction.atomic():
                vinculadas = sum(vincular_marcacoes_sem_funcionario(equipamento) for equipamento in equipamentos)

                self.stdout.write(self.style.SUCCESS(
                    f'{vinculadas} marcação(ões) {"prontas pra vincular" if dry_run else "vinculadas"}.'
                ))

                if dry_run:
                    self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()
        except DryRunRollback:
            pass
