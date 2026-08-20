from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from funcionarios.models import Funcionario, Usuario


class DryRunRollback(Exception):
    pass


class Command(BaseCommand):
    help = 'Gera acesso ao sistema (User + Usuario) para todos os funcionários que ainda não têm, usando a senha padrão do .env'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        acessos_gerados = 0
        sem_cpf = 0

        funcionarios_sem_acesso = Funcionario.objects.filter(usuario__isnull=True)

        try:
            with transaction.atomic():
                for funcionario in funcionarios_sem_acesso:
                    if not funcionario.cpf:
                        sem_cpf += 1
                        continue

                    user = User.objects.create_user(
                        username=funcionario.cpf,
                        password=settings.SENHA_PADRAO_ACESSO,
                    )

                    Usuario.objects.create(
                        user=user,
                        funcionario=funcionario,
                        papel='colaborador',
                        senha_provisoria=True,
                    )
                    acessos_gerados += 1

                self.stdout.write(self.style.SUCCESS(
                    f'Acessos gerados: {acessos_gerados}\n'
                    f'Funcionários sem CPF (ignorados): {sem_cpf}'
                ))

                if dry_run:
                    self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()

        except DryRunRollback:
            pass
