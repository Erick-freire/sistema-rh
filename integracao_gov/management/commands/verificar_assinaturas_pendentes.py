from django.core.management.base import BaseCommand

from integracao_gov.services import verificar_status_pendentes


class Command(BaseCommand):
    help = (
        'Consulta a API de assinatura gov.br pelo status das solicitações pendentes. '
        'Só é necessário se a API não suportar webhook de status (ver especificação, seção 5.2).'
    )

    def handle(self, *args, **options):
        atualizados = verificar_status_pendentes()
        self.stdout.write(self.style.SUCCESS(f'{atualizados} solicitação(ões) atualizada(s).'))
