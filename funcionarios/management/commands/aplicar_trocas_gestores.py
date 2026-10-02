from django.core.management.base import BaseCommand
from django.db import transaction

from funcionarios.models import Funcionario, Setor, Usuario

# (nome do setor, matrícula do gestor, nome esperado -- só para conferência no relatório)
TROCAS = [
    ('COMPRAS/LOGISTICA ENTRADA', '484', 'DANIEL DE SOUSA VIEIRA'),
    ('FATURAMENTO', '101', 'MARCELO SIQUEIRA MOREIRA'),
    ('LOGISTICA (SAIDA)', '1041', 'JORGE ANSELMO NETTO'),
    ('LIMPEZA ADMINISTRAÇÃO', '1185', 'SAVANNA DA SILVA LIMA DIAS'),
    ('PORTARIA', '1185', 'SAVANNA DA SILVA LIMA DIAS'),
    ('RECURSOS HUMANOS', '1185', 'SAVANNA DA SILVA LIMA DIAS'),
    ('SEGURANCA DO TRABALHO', '1185', 'SAVANNA DA SILVA LIMA DIAS'),
]


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""
    pass


class Command(BaseCommand):
    help = (
        'Aplica as trocas de gestor de setor definidas a partir da planilha de setembro, '
        'casando o gestor sempre por matrícula (nunca por nome, para evitar ambiguidade '
        'entre homônimos). Idempotente: rodar de novo não muda nada.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Só mostra o relatório, não grava nada.')
        parser.add_argument(
            '--promover-gestores',
            action='store_true',
            help="Também marca papel='gestor' no Usuario de cada gestor definido aqui.",
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        promover = options['promover_gestores']

        aplicadas = []
        ja_corretas = []
        promovidos = []
        erros = []

        try:
            with transaction.atomic():
                for nome_setor, matricula, nome_esperado in TROCAS:
                    setor = Setor.objects.filter(nome__iexact=nome_setor).order_by('pk').first()
                    if setor is None:
                        erros.append(f'Setor "{nome_setor}" não encontrado -- pulado.')
                        continue

                    gestor = Funcionario.objects.filter(matricula=matricula).first()
                    if gestor is None:
                        erros.append(
                            f'Funcionário de matrícula {matricula} ({nome_esperado}) não encontrado '
                            f'-- setor "{nome_setor}" deixado como está.'
                        )
                        continue

                    if gestor.nome.strip().upper() != nome_esperado.strip().upper():
                        erros.append(
                            f'Matrícula {matricula} é "{gestor.nome}", esperava "{nome_esperado}" '
                            f'-- setor "{nome_setor}" deixado como está por segurança.'
                        )
                        continue

                    anterior = setor.gestor
                    if anterior and anterior.pk == gestor.pk:
                        ja_corretas.append(f'{setor.nome}: já é {gestor.nome} (matrícula {matricula})')
                    else:
                        setor.gestor = gestor
                        setor.save(update_fields=['gestor'])
                        aplicadas.append(
                            f'{setor.nome}: {anterior.nome if anterior else "(sem gestor)"} '
                            f'-> {gestor.nome} (matrícula {matricula})'
                        )

                    if promover:
                        usuario = Usuario.objects.filter(funcionario=gestor).first()
                        if usuario is None:
                            erros.append(f'{gestor.nome} (matrícula {matricula}): sem Usuario, não dá para promover a gestor.')
                        elif usuario.papel != 'gestor':
                            usuario.papel = 'gestor'
                            usuario.save(update_fields=['papel'])
                            promovidos.append(f'{gestor.nome} (matrícula {matricula})')

                self._relatar(aplicadas, ja_corretas, promovidos, erros, promover)

                if dry_run:
                    self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()

        except DryRunRollback:
            pass

    def _relatar(self, aplicadas, ja_corretas, promovidos, erros, promover):
        escrever = self.stdout.write

        escrever('')
        escrever(self.style.MIGRATE_HEADING(f'=== TROCAS APLICADAS ({len(aplicadas)}) ==='))
        for item in aplicadas or ['(nenhuma)']:
            escrever(f'  - {item}')

        escrever('')
        escrever(self.style.MIGRATE_HEADING(f'=== JÁ ESTAVAM CORRETAS ({len(ja_corretas)}) ==='))
        for item in ja_corretas or ['(nenhuma)']:
            escrever(f'  - {item}')

        if promover:
            escrever('')
            escrever(self.style.MIGRATE_HEADING(f'=== PROMOVIDOS A papel=gestor ({len(promovidos)}) ==='))
            for item in promovidos or ['(nenhum -- todos já eram gestor)']:
                escrever(f'  - {item}')

        escrever('')
        if erros:
            escrever(self.style.ERROR(f'=== NÃO APLICADAS ({len(erros)}) ==='))
            for item in erros:
                escrever(f'  - {item}')
        else:
            escrever(self.style.SUCCESS('Todas as 7 linhas foram resolvidas sem erro.'))
        escrever('')
