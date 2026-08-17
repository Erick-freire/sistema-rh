from django.core.management.base import BaseCommand

from funcionarios.models import Funcionario
from ponto.services import enfileirar_sincronizacao


class Command(BaseCommand):
    help = (
        'Enfileira sincronização com a Control iD para funcionários que já existiam no banco '
        'ANTES do módulo de Ponto ser instalado. O gatilho automático (signal) só dispara para '
        'funcionários criados/editados depois disso -- rode este comando uma única vez ao implantar '
        'o módulo pela primeira vez num banco com funcionários já cadastrados.'
    )

    def handle(self, *args, **options):
        total = 0
        for funcionario in Funcionario.objects.all():
            enfileirar_sincronizacao(funcionario)
            total += 1

        self.stdout.write(self.style.SUCCESS(
            f'{total} funcionário(s) verificado(s). Rode "sincronizar_pendencias_control_id" '
            '(ou clique em Sincronizar na tela de Equipamentos) para enviar de fato aos relógios.'
        ))
