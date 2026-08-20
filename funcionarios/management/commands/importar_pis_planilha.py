import re

import openpyxl
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, transaction

from funcionarios.models import Funcionario


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""
    pass


def _so_digitos(valor):
    return re.sub(r'\D', '', str(valor or ''))


class Command(BaseCommand):
    help = (
        'Importa o PIS de cada funcionário a partir de uma planilha de RH (com colunas Nome/CPF/PIS, '
        'ex.: "Lista - Cadastro de Catracas e Horários.xlsx"), casando por CPF. Só preenche quem ainda '
        'não tem PIS cadastrado -- nunca sobrescreve um PIS já preenchido no sistema.'
    )

    def add_arguments(self, parser):
        parser.add_argument('caminho_planilha', type=str)
        parser.add_argument('--dry-run', action='store_true', help='Só mostra o relatório, não grava nada.')

    def _achar_aba(self, wb):
        for nome_aba in wb.sheetnames:
            ws = wb[nome_aba]
            primeira_linha = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), [])
            cabecalho = [str(c or '').strip().lower() for c in primeira_linha]
            if 'nome' in cabecalho and 'cpf' in cabecalho and 'pis' in cabecalho:
                return ws, cabecalho.index('nome'), cabecalho.index('cpf'), cabecalho.index('pis')
        return None, None, None, None

    def handle(self, *args, **options):
        caminho = options['caminho_planilha']
        dry_run = options['dry_run']

        try:
            wb = openpyxl.load_workbook(caminho, data_only=True)
        except FileNotFoundError:
            raise CommandError(f'Planilha não encontrada: {caminho}')

        aba, indice_nome, indice_cpf, indice_pis = self._achar_aba(wb)
        if aba is None:
            raise CommandError('Nenhuma aba da planilha tem as colunas Nome, CPF e PIS no cabeçalho.')

        funcionarios_por_cpf = {_so_digitos(f.cpf): f for f in Funcionario.objects.all()}

        atualizados = 0
        pis_invalido = []
        ja_tinha_diferente = []
        duplicado = []

        try:
            with transaction.atomic():
                for linha in aba.iter_rows(min_row=2, values_only=True):
                    nome = linha[indice_nome]
                    cpf_d = _so_digitos(linha[indice_cpf])
                    pis_d = _so_digitos(linha[indice_pis])

                    if not cpf_d:
                        continue
                    funcionario = funcionarios_por_cpf.get(cpf_d)
                    if not funcionario:
                        continue  # quem não existe no RH não é o escopo deste comando

                    if len(pis_d) != 11 or pis_d == '00000000000':
                        pis_invalido.append(nome)
                        continue

                    if funcionario.pis:
                        if funcionario.pis != pis_d:
                            ja_tinha_diferente.append((funcionario.nome, funcionario.pis, pis_d))
                        continue

                    if dry_run:
                        atualizados += 1
                        continue

                    try:
                        with transaction.atomic():
                            funcionario.pis = pis_d
                            funcionario.save(update_fields=['pis'])
                        atualizados += 1
                    except IntegrityError:
                        duplicado.append((funcionario.nome, pis_d))

                self.stdout.write(self.style.SUCCESS(
                    f'{atualizados} funcionário(s) {"prontos pra atualizar" if dry_run else "atualizados"} com PIS.'
                ))
                if pis_invalido:
                    self.stdout.write(self.style.WARNING(f'{len(pis_invalido)} sem PIS válido na planilha (em branco ou zerado):'))
                    for nome in pis_invalido:
                        self.stdout.write(f'  - {nome}')
                if ja_tinha_diferente:
                    self.stdout.write(self.style.WARNING(f'{len(ja_tinha_diferente)} já tinham PIS diferente no sistema (não sobrescrito, confira manualmente):'))
                    for nome, atual, novo in ja_tinha_diferente:
                        self.stdout.write(f'  - {nome}: sistema tem {atual}, planilha tem {novo}')
                if duplicado:
                    self.stdout.write(self.style.ERROR(f'{len(duplicado)} com PIS que já pertence a outro funcionário no sistema (não gravado):'))
                    for nome, pis in duplicado:
                        self.stdout.write(f'  - {nome}: {pis}')

                if dry_run:
                    self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()
        except DryRunRollback:
            pass
