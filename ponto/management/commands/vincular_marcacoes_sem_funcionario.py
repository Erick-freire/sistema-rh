from django.core.management.base import BaseCommand
from django.db import transaction

from funcionarios.models import Funcionario
from ponto.models import Equipamento, MarcacaoRelogio


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""
    pass


class Command(BaseCommand):
    help = (
        'Revincula marcações já coletadas que ficaram sem funcionário (identificador do equipamento não '
        'batia com nenhum CPF/PIS conhecido na hora da coleta) -- útil depois de importar o PIS de mais '
        'gente (ver importar_pis_planilha), sem precisar coletar tudo de novo do equipamento.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--equipamento', type=str, help='Nome do equipamento (padrão: todos).')
        parser.add_argument('--dry-run', action='store_true', help='Só mostra o relatório, não grava nada.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        marcacoes = MarcacaoRelogio.objects.filter(funcionario__isnull=True)
        if options['equipamento']:
            marcacoes = marcacoes.filter(equipamento__nome=options['equipamento'])

        funcionarios_por_identificador = {f.cpf: f.id for f in Funcionario.objects.exclude(cpf='')}
        funcionarios_por_identificador.update(
            {f.pis: f.id for f in Funcionario.objects.exclude(pis__isnull=True).exclude(pis='')}
        )

        vinculadas = 0
        lote = []
        try:
            with transaction.atomic():
                for marcacao in marcacoes.iterator(chunk_size=2000):
                    identificador = (marcacao.dados_brutos or {}).get('identificador')
                    funcionario_id = funcionarios_por_identificador.get(identificador)
                    if not funcionario_id:
                        continue

                    vinculadas += 1
                    if dry_run:
                        continue

                    marcacao.funcionario_id = funcionario_id
                    lote.append(marcacao)
                    if len(lote) >= 2000:
                        MarcacaoRelogio.objects.bulk_update(lote, ['funcionario_id'], batch_size=2000)
                        lote = []

                if lote:
                    MarcacaoRelogio.objects.bulk_update(lote, ['funcionario_id'], batch_size=2000)

                self.stdout.write(self.style.SUCCESS(
                    f'{vinculadas} marcação(ões) {"prontas pra vincular" if dry_run else "vinculadas"}.'
                ))

                if dry_run:
                    self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()
        except DryRunRollback:
            pass
