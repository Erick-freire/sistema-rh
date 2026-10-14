import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


def _base64url(dados):
    return base64.urlsafe_b64encode(dados).rstrip(b'=').decode('ascii')


class Command(BaseCommand):
    help = (
        'Gera o par de chaves VAPID das notificações push e imprime as linhas para o .env. '
        'Só precisa rodar uma vez: trocar as chaves depois invalida a inscrição de todos os aparelhos.'
    )

    def handle(self, *args, **options):
        privada = ec.generate_private_key(ec.SECP256R1())
        bytes_privada = privada.private_numbers().private_value.to_bytes(32, 'big')
        bytes_publica = privada.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint,
        )
        self.stdout.write('Cole no .env e reinicie o servidor:\n')
        self.stdout.write(f'VAPID_PUBLIC_KEY={_base64url(bytes_publica)}')
        self.stdout.write(f'VAPID_PRIVATE_KEY={_base64url(bytes_privada)}')
