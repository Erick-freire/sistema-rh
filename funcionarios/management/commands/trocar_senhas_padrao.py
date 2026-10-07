import secrets

from django.core.management.base import BaseCommand
from django.db import transaction

from funcionarios.models import Usuario


class Command(BaseCommand):
    help = (
        'Troca por uma senha aleatória (que ninguém fica sabendo) a senha de quem ainda '
        'está com a senha padrão antiga e nunca a trocou. O acesso volta pelo "Gerar nova '
        'senha" do RH/gestor ou pelo "Recuperar acesso" da tela de login.'
    )

    def add_arguments(self, parser):
        parser.add_argument('senha_antiga', help='A senha padrão que era usada (a antiga SENHA_PADRAO_ACESSO do .env).')
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        senha_antiga = options['senha_antiga']

        provisorias = Usuario.objects.filter(senha_provisoria=True).select_related('user', 'funcionario')
        alvo = [usuario for usuario in provisorias if usuario.user.check_password(senha_antiga)]

        gestores = [usuario for usuario in alvo if usuario.papel != 'colaborador' or usuario.acesso_rh]
        self.stdout.write(f'Contas com a senha antiga: {len(alvo)} ({len(gestores)} com papel além de colaborador)')
        for usuario in gestores:
            self.stdout.write(f'  - {usuario.get_papel_display()}: {usuario.funcionario.nome}')

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
            return

        with transaction.atomic():
            for usuario in alvo:
                usuario.user.set_password(secrets.token_urlsafe(16))
                usuario.user.save(update_fields=['password'])

        self.stdout.write(self.style.SUCCESS(f'Senhas trocadas: {len(alvo)}'))
