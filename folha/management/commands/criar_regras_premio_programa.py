"""
Cria as regras de prêmio programa (cargo = valor, com a lista de quem recebe)
a partir de um fechamento já feito -- o de set/2026, importado da planilha
enviada à contabilidade (pedido do usuário em 29/10/2026).

Quem recebeu menos que o valor cheio do cargo naquele mês (atestado, falta)
entra na regra do valor cheio: a redução do mês continua manual na grade.
Pode rodar de novo: reaproveita as regras que existem e não mexe em quem já
está numa regra.

    python manage.py criar_regras_premio_programa --competencia 2026-09 --dry-run
"""
from collections import defaultdict
from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from folha.models import Fechamento, RegraPremio
from folha.premios import criar_regras_programa, planejar_regras_programa
from funcionarios.utils import formatar_moeda


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""


class Command(BaseCommand):
    help = 'Cria as regras de prêmio programa (por cargo, com a lista de quem recebe) a partir de um fechamento.'

    def add_arguments(self, parser):
        parser.add_argument('--competencia', required=True, help='Mês do fechamento de referência, AAAA-MM (ex.: 2026-09).')
        parser.add_argument('--dry-run', action='store_true', help='Mostra o que faria sem gravar nada.')

    def handle(self, *args, **options):
        try:
            ano, mes = map(int, options['competencia'].split('-'))
            competencia = date(ano, mes, 1)
        except ValueError:
            raise CommandError('Competência inválida: use AAAA-MM (ex.: 2026-09).')
        fechamento = Fechamento.objects.filter(competencia=competencia).first()
        if fechamento is None:
            raise CommandError(f'Não há fechamento de {mes:02d}/{ano}. Importe a planilha antes (importar_fechamento_planilha).')

        plano = planejar_regras_programa(fechamento)
        try:
            with transaction.atomic():
                # O programa saiu de `RegraPremio` (setor/cargo/pessoa): o que sobrou lá
                # não vale mais, e as regras deste comando tomam o lugar.
                antigas, _ = RegraPremio.objects.filter(tipo='premio_programa').delete()
                criadas, incluidas, puladas = criar_regras_programa(plano)
                self._relatorio(fechamento, plano, criadas, incluidas, puladas, antigas)
                if options['dry_run']:
                    self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()
        except DryRunRollback:
            pass

    def _relatorio(self, fechamento, plano, criadas, incluidas, puladas, antigas):
        escreva = self.stdout.write
        escreva(self.style.SUCCESS(
            f'{len(plano)} pessoa(s) com prêmio programa em {fechamento.competencia_extenso}: '
            f'{len(criadas)} regra(s) criada(s), {incluidas} pessoa(s) incluída(s).'
        ))
        if antigas:
            escreva(self.style.WARNING(f'{antigas} regra(s) de programa do modelo antigo (setor/cargo/pessoa) apagada(s).'))

        por_regra = defaultdict(list)
        for item in plano:
            por_regra[(item['cargo'], item['valor'])].append(item)
        escreva('\nRegras criadas:')
        for regra in sorted(criadas, key=lambda r: (r.cargo.casefold(), r.valor)):
            escreva(f'  {regra.cargo:<50} {formatar_moeda(regra.valor):>12}  {regra.membros.count():>3} pessoa(s)')

        reduzidos = [item for item in plano if item['reduzido']]
        if reduzidos:
            escreva(f'\n{len(reduzidos)} pessoa(s) receberam menos que o valor cheio (redução do mês) e entraram na regra cheia:')
            for item in reduzidos:
                linha = item['linha']
                aviso = f'  << confira: {item["confira"]}' if item['confira'] else ''
                escreva(
                    f'  {linha.matricula:>5} {linha.nome[:38]:<38} {linha.funcionario.setor.nome[:22]:<22} '
                    f'recebeu {formatar_moeda(linha.premio_programa):>11} -> regra de {formatar_moeda(item["valor"])}{aviso}'
                )
        if puladas:
            escreva(f'\n{len(puladas)} pessoa(s) já estavam numa regra e ficaram onde estavam:')
            for linha in puladas:
                escreva(f'  {linha.matricula:>5} {linha.nome}')
