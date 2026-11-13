from django.apps import AppConfig


class FuncionariosConfig(AppConfig):
    name = 'funcionarios'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in

        from .middleware import marcar_inicio_do_login

        # Marca a hora do login para o limite de 12 horas (`LimiteDoLoginMiddleware`).
        user_logged_in.connect(marcar_inicio_do_login, dispatch_uid='funcionarios.marcar_inicio_do_login')
