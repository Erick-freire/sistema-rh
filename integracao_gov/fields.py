import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models


def _obter_fernet():
    chave = settings.CHAVE_CIFRA_SEGREDOS

    if not chave:
        if not settings.DEBUG:
            # Em produção, exige chave dedicada -- sem isso, a cifra cairia
            # numa chave derivável da SECRET_KEY, que pode estar no
            # histórico do repositório (ver PRODUCAO.md).
            raise ImproperlyConfigured(
                'CHAVE_CIFRA_SEGREDOS não definida no .env. Gere uma com '
                '`python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"` antes de rodar em produção.'
            )
        # Sem chave dedicada configurada, deriva uma a partir da SECRET_KEY do projeto.
        chave = base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest())
    elif isinstance(chave, str):
        chave = chave.encode()

    return Fernet(chave)


class EncryptedTextField(models.TextField):
    """Texto cifrado em repouso (Fernet). Usado por Equipamento.senha_admin (app ponto) — nunca deve ser exibido em views/admin."""

    def from_db_value(self, value, expression, connection):
        return self.to_python(value)

    def to_python(self, value):
        if value is None or value == '':
            return value

        try:
            return _obter_fernet().decrypt(value.encode()).decode()
        except (InvalidToken, ValueError):
            return value

    def get_prep_value(self, value):
        if value is None or value == '':
            return value

        return _obter_fernet().encrypt(str(value).encode()).decode()
