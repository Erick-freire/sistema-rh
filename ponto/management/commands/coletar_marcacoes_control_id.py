from django.core.management.base import BaseCommand

from ponto.controlid_client import ControlIdAPIError
from ponto.models import Equipamento
from ponto.services import coletar_marcacoes


class Command(BaseCommand):
    help = (
        'Coleta incrementalmente as marcações de todos os equipamentos Control iD ativos. '
        'Idempotente: pode rodar quantas vezes quiser sem duplicar marcações. '
        'Pensado para rodar periodicamente via agendador de tarefas.'
    )

    def handle(self, *args, **options):
        total_coletadas = 0

        for equipamento in Equipamento.objects.filter(ativo=True):
            try:
                coletadas = coletar_marcacoes(equipamento)
            except ControlIdAPIError as erro:
                self.stdout.write(self.style.WARNING(f'{equipamento.nome}: falhou ({erro}). Segue para o próximo.'))
                continue
            except Exception as erro:  # nunca deixa um equipamento problemático derrubar os demais
                self.stdout.write(self.style.ERROR(f'{equipamento.nome}: erro inesperado ({erro}). Segue para o próximo.'))
                continue

            total_coletadas += coletadas
            self.stdout.write(f'{equipamento.nome}: {coletadas} marcação(ões) nova(s).')

        self.stdout.write(self.style.SUCCESS(f'Total: {total_coletadas} marcação(ões) coletada(s).'))
