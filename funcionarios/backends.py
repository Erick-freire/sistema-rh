from django.contrib.auth.backends import ModelBackend

from .models import Usuario


class CpfOuLoginGestorBackend(ModelBackend):
    """Login pelo CPF (todo mundo) ou pelo primeiro nome (só gestor).

    O `User.username` continua sendo o CPF; o login de gestor é só um apelido
    que aponta para ele. Deixando de ser gestor, o apelido some (ver
    `Usuario.save`) e sobra o CPF.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username:
            gestor = Usuario.objects.filter(
                papel='gestor', login_gestor=username.strip().lower(),
            ).select_related('user').first()
            if gestor:
                username = gestor.user.username
        return super().authenticate(request, username=username, password=password, **kwargs)
