from django.core.management.base import BaseCommand
from django.db import transaction

from ponto.models import MarcacaoRelogio
from ponto.services import funcionario_da_marcacao, funcionarios_por_identificador


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""
    pass


class Command(BaseCommand):
    help = (
        'Revincula marcações já coletadas que ficaram sem funcionário (identificador do equipamento não '
        'batia com ninguém conhecido na hora da coleta) -- útil depois de importar o PIS de mais gente '
        '(ver importar_pis_planilha), de cadastrar quem já batia ponto ou de rodar '
        'reconciliar_funcionarios_equipamento, sem precisar coletar tudo de novo do equipamento. '
        'Usa a mesma regra da coleta (ver ponto.services.funcionario_da_marcacao).'
    )

    def add_arguments(self, parser):
        parser.add_argument('--equipamento', type=str, help='Nome do equipamento (padrão: todos).')
        parser.add_argument('--dry-run', action='store_true', help='Só mostra o relatório, não grava nada.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        marcacoes = MarcacaoRelogio.objects.filter(funcionario__isnull=True)
        if options['equipamento']:
            marcacoes = marcacoes.filter(equipamento__nome=options['equipamento'])

        mapas_por_equipamento = {}

        vinculadas = 0
        lote = []
        try:
            with transaction.atomic():
                for marcacao in marcacoes.iterator(chunk_size=2000):
                    if marcacao.equipamento_id not in mapas_por_equipamento:
                        mapas_por_equipamento[marcacao.equipamento_id] = funcionarios_por_identificador(
                            marcacao.equipamento_id,
                        )
                    funcionario_id = funcionario_da_marcacao(
                        marcacao.dados_brutos or {}, mapas_por_equipamento[marcacao.equipamento_id],
                    )
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
