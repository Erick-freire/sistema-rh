import csv
from datetime import datetime
from django.core.management.base import BaseCommand
from django.db import transaction
from funcionarios.models import Funcionario, SolicitacaoFerias


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""
    pass


class Command(BaseCommand):
    help = 'Importa data de admissao e ultima ferias registradas no Secullum'

    def add_arguments(self, parser):
        parser.add_argument('caminho_admissao', type=str)
        parser.add_argument('caminho_ferias', type=str)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        admissoes_atualizadas = 0
        admissoes_nao_encontradas = 0
        admissoes_ignoradas_demitidos = 0
        ferias_criadas = 0
        ferias_atualizadas = 0
        ferias_nao_encontradas = 0
        ferias_ignoradas_demitidos = 0

        try:
            with transaction.atomic():
                with open(options['caminho_admissao'], encoding='utf-8-sig') as arquivo:
                    leitor = csv.DictReader(arquivo, delimiter='\t')
                    for linha in leitor:
                        cpf_bruto = linha['cpf'].strip()

                        if cpf_bruto.lower().startswith('d'):
                            admissoes_ignoradas_demitidos += 1
                            continue

                        cpf = self._limpar_cpf(cpf_bruto)
                        data_admissao = self._parse_data(linha['admissao'])

                        try:
                            funcionario = Funcionario.objects.get(cpf=cpf)
                        except Funcionario.DoesNotExist:
                            admissoes_nao_encontradas += 1
                            continue

                        funcionario.data_admissao = data_admissao
                        funcionario.save()
                        admissoes_atualizadas += 1

                with open(options['caminho_ferias'], encoding='utf-8-sig') as arquivo:
                    leitor = csv.DictReader(arquivo, delimiter='\t')
                    for linha in leitor:
                        cpf_bruto = linha['cpf'].strip()

                        if cpf_bruto.lower().startswith('d'):
                            ferias_ignoradas_demitidos += 1
                            continue

                        cpf = self._limpar_cpf(cpf_bruto)
                        data_inicio = self._parse_data(linha['data_inicio'])
                        data_fim = self._parse_data(linha['data_fim'])
                        dias = (data_fim - data_inicio).days

                        try:
                            funcionario = Funcionario.objects.get(cpf=cpf)
                        except Funcionario.DoesNotExist:
                            ferias_nao_encontradas += 1
                            continue

                        solicitacao, criada = SolicitacaoFerias.objects.update_or_create(
                            funcionario=funcionario,
                            data_solicitada=data_inicio,
                            defaults={
                                'dias_solicitados': dias,
                                'status': 'aprovada',
                            }
                        )
                        if criada:
                            ferias_criadas += 1
                        else:
                            ferias_atualizadas += 1

                self.stdout.write(self.style.SUCCESS(
                    f'Admissões atualizadas: {admissoes_atualizadas}\n'
                    f'Admissões sem funcionário correspondente: {admissoes_nao_encontradas}\n'
                    f'Admissões ignoradas (demitidos): {admissoes_ignoradas_demitidos}\n'
                    f'Férias criadas: {ferias_criadas}\n'
                    f'Férias atualizadas: {ferias_atualizadas}\n'
                    f'Férias sem funcionário correspondente: {ferias_nao_encontradas}\n'
                    f'Férias ignoradas (demitidos): {ferias_ignoradas_demitidos}'
                ))

                if dry_run:
                    self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()

        except DryRunRollback:
            pass

    def _limpar_cpf(self, cpf_bruto):
        return ''.join(filter(str.isdigit, str(cpf_bruto or '')))

    def _parse_data(self, valor):
        return datetime.strptime(valor.strip(), '%Y-%m-%d %H:%M:%S').date()