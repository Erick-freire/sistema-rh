"""
Importa uma planilha de fechamento já enviada à contabilidade (feita no Excel,
antes do sistema) como um fechamento FECHADO. Serve de ponto de partida: o
mês seguinte, aberto no sistema, copia dela os lançamentos do RH.

Também grava a coluna EMP no cadastro (`Funcionario.codigo_empresa`), que é
de onde os fechamentos seguintes tiram esse código.

    python manage.py importar_fechamento_planilha "FOLHA SETEMBRO 2026 - CONTABILIDADE.xlsx" --competencia 2026-09
"""
import unicodedata
from datetime import date
from decimal import Decimal

import openpyxl
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from folha.colunas import COLUNAS, SEIS_PORCENTO
from folha.models import Fechamento, LinhaFechamento
from folha.services import atualizar_cadastro
from funcionarios.models import Funcionario


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""


def _normalizar(texto):
    sem_acento = unicodedata.normalize('NFKD', str(texto or '')).encode('ascii', 'ignore').decode()
    return ' '.join(sem_acento.upper().replace('|', ' ').split())


def _decimal(valor):
    return None if valor in (None, '') else Decimal(str(valor)).quantize(Decimal('0.01'))


def _minutos(valor):
    """Horas.minutos da planilha (6.2 = 6h20) -> minutos."""
    if valor in (None, ''):
        return 0
    horas = int(valor)
    return horas * 60 + round((float(valor) - horas) * 100)


def _sim(valor):
    return _normalizar(valor) == 'SIM'


class Command(BaseCommand):
    help = 'Importa uma planilha de fechamento de folha (layout da contabilidade) como fechamento fechado.'

    def add_arguments(self, parser):
        parser.add_argument('caminho_arquivo')
        parser.add_argument('--competencia', required=True, help='Mês da planilha, no formato AAAA-MM.')
        parser.add_argument('--dry-run', action='store_true', help='Simula sem gravar nada no banco.')

    def handle(self, *args, **options):
        try:
            ano, mes = map(int, options['competencia'].split('-'))
            competencia = date(ano, mes, 1)
        except ValueError:
            raise CommandError('Competência inválida: use AAAA-MM (ex.: 2026-09).')
        if Fechamento.objects.filter(competencia=competencia).exists():
            raise CommandError(f'Já existe fechamento para {mes:02d}/{ano}. Exclua-o pela tela antes de importar de novo.')

        aba = openpyxl.load_workbook(options['caminho_arquivo'], data_only=True).worksheets[0]
        cabecalho = [_normalizar(celula.value) for celula in aba[1]][:len(COLUNAS)]
        esperado = [_normalizar(coluna.cabecalho) for coluna in COLUNAS]
        if cabecalho != esperado:
            diferentes = [f'{e!r} x {c!r}' for e, c in zip(esperado, cabecalho) if e != c]
            raise CommandError('A planilha não está no layout do fechamento: ' + '; '.join(diferentes))

        funcionarios = {f.matricula: f for f in Funcionario.objects.exclude(matricula=None)}
        nao_encontrados, codigos_alterados, importadas = [], 0, 0

        try:
            with transaction.atomic():
                fechamento = Fechamento.objects.create(
                    competencia=competencia, status='fechado', fechado_em=timezone.now(),
                    observacao='Importado da planilha enviada à contabilidade',
                )
                for linha_planilha in aba.iter_rows(min_row=2, values_only=True):
                    valores = dict(zip((coluna.campo for coluna in COLUNAS), linha_planilha))
                    if valores['matricula'] in (None, '') or not valores['nome']:
                        continue
                    funcionario = funcionarios.get(str(valores['matricula']).strip())
                    if funcionario is None:
                        nao_encontrados.append(f'{valores["matricula"]} {" ".join(str(valores["nome"]).split())}')
                        continue

                    if valores['codigo_empresa'] and funcionario.codigo_empresa != int(valores['codigo_empresa']):
                        funcionario.codigo_empresa = int(valores['codigo_empresa'])
                        funcionario.save(update_fields=['codigo_empresa'])
                        codigos_alterados += 1

                    vt = valores['vale_transporte']
                    periculosidade = _decimal(valores['periculosidade'])
                    linha = LinhaFechamento(
                        fechamento=fechamento, funcionario=funcionario,
                        descontos=_decimal(valores['descontos']),
                        decimo_terceiro=_sim(valores['decimo_terceiro']),
                        premio_programa=_decimal(valores['premio_programa']),
                        premio_bonus=_decimal(valores['premio_bonus']),
                        premio_incentivo=_decimal(valores['premio_incentivo']),
                        premio_safra=_decimal(valores['premio_safra']),
                        reembolso_despesas=_decimal(valores['reembolso_despesas']),
                        comissao=_decimal(valores['comissao']),
                        adiantamento_comissao=_decimal(valores['adiantamento_comissao']),
                        # 0,06 formatado como 6% = desconto de 6%; qualquer outro número é o valor em R$.
                        vt_seis_porcento=vt is not None and Decimal(str(vt)) == SEIS_PORCENTO,
                        vt_valor=None if vt is None or Decimal(str(vt)) == SEIS_PORCENTO else _decimal(vt),
                        # Na planilha vem como fração (0,3 formatado como 30%).
                        periculosidade=periculosidade * 100 if periculosidade is not None else None,
                        adicional_noturno_minutos=_minutos(valores['adicional_noturno_minutos']),
                        he_50_minutos=_minutos(valores['he_50_minutos']),
                        he_100_minutos=_minutos(valores['he_100_minutos']),
                        faltas_minutos=_minutos(valores['faltas_minutos']),
                        faltas_dias=int(valores['faltas_dias'] or 0),
                        atestados_dias=int(valores['atestados_dias'] or 0),
                        assiduidade=_sim(valores['assiduidade']),
                        # Automáticos "vazios": horas e prêmios vieram da planilha, e um
                        # recálculo (se reabrirem) não passa por cima deles.
                        ponto_calculado={'valores': {}},
                        premios_aplicados={'valores': {}},
                        ponto_pendente=False,
                    )
                    atualizar_cadastro(linha)
                    linha.save()
                    importadas += 1

                self.stdout.write(self.style.SUCCESS(
                    f'Fechamento de {mes:02d}/{ano} importado com {importadas} funcionário(s).\n'
                    f'Código da empresa atualizado no cadastro de {codigos_alterados} funcionário(s).'
                ))
                if nao_encontrados:
                    self.stdout.write(self.style.WARNING(
                        f'{len(nao_encontrados)} linha(s) sem funcionário com essa matrícula no sistema (ficaram de fora):\n  '
                        + '\n  '.join(nao_encontrados)
                    ))
                if options['dry_run']:
                    self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()
        except DryRunRollback:
            pass
