import re

from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import User

from .models import Usuario

CPF_DIGITADO = re.compile(r'\s*[\d.\-\s]+\s*')


class CpfOuLoginGestorBackend(ModelBackend):
    """Login pelo CPF (todo mundo) ou pelo primeiro nome (só gestor).

    O `User.username` continua sendo o CPF; o login de gestor é só um apelido
    que aponta para ele. Deixando de ser gestor, o apelido some (ver
    `Usuario.save`) e sobra o CPF.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username and CPF_DIGITADO.fullmatch(username):
            # O CPF pode ser digitado com ou sem pontuação, e há usernames
            # antigos gravados com pontuação: vale o formato que existir.
            digitos = re.sub(r'\D', '', username)
            candidatos = [digitos]
            if len(digitos) == 11:
                candidatos.append(f'{digitos[:3]}.{digitos[3:6]}.{digitos[6:9]}-{digitos[9:]}')
            existente = User.objects.filter(username__in=candidatos).values_list('username', flat=True).first()
            username = existente or username.strip()
        elif username:
            gestor = Usuario.objects.filter(
                papel='gestor', login_gestor=username.strip().lower(),
            ).select_related('user').first()
            if gestor:
                username = gestor.user.username
        return super().authenticate(request, username=username, password=password, **kwargs)
