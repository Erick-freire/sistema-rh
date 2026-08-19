import re
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from pypdf import PdfReader

from funcionarios.models import Funcionario, SolicitacaoFerias


DATE_RE = re.compile(r'^\d{2}/\d{2}/\d{4}$')
NUM_RE = re.compile(r'^\d+(?:,\d{1,2})?$')
NOME_COLADO_EM_DATA_RE = re.compile(r'([A-Za-zÀ-ÿ])(\d{2}/\d{2}/\d{4})')

PREFIXOS_IGNORADOS = (
    'Períodos Aquisitivos', 'Contrato do Empregado', 'Total Per', '_', '0453-SOTRIGO',
)


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""
    pass


def is_date(tok):
    return bool(DATE_RE.match(tok))


def is_num(tok):
    return bool(NUM_RE.match(tok))


def parse_data(tok):
    return datetime.strptime(tok, '%d/%m/%Y').date()


def parse_dias(tok):
    return int(round(float(tok.replace(',', '.'))))


def somar_anos(data, anos):
    try:
        return data.replace(year=data.year + anos)
    except ValueError:
        return data.replace(year=data.year + anos, day=28)


def calcular_ancora_periodos(periodos_unicos):
    """O relatório só lista períodos aquisitivos recentes (data final entre 2025 e 2027),
    nunca o histórico completo desde a admissão. Se o cálculo de vencimento partir da
    admissão real de um funcionário antigo, ele tentaria reconstruir anos de férias que
    não foram importados e concluiria (errado) que o primeiro período, de décadas atrás,
    segue em aberto. Por isso a âncora do ciclo é sempre o início do período listado mais
    antigo a partir do qual a sequência se mantém consistente (1 ano após o outro); se a
    sequência tiver uma quebra no meio (reinício de contagem por afastamento prolongado,
    por exemplo), a âncora passa a ser o período pós-quebra, descartando o que veio antes."""
    if not periodos_unicos:
        return None

    ancora = parse_data(periodos_unicos[0][0])
    anterior = ancora
    for ini_str, _fim_str in periodos_unicos[1:]:
        ini = parse_data(ini_str)
        if ini != somar_anos(anterior, 1):
            ancora = ini
        anterior = ini
    return ancora


class Command(BaseCommand):
    help = (
        'Importa períodos aquisitivos e recibos de férias a partir do relatório em PDF '
        '"Períodos Aquisitivos X Recibos Férias" (por matrícula/contrato) e cria/atualiza as '
        'SolicitacaoFerias correspondentes (status=aprovada), além de preencher a data de '
        'admissão quando ainda estiver vazia no cadastro.'
    )

    def add_arguments(self, parser):
        parser.add_argument('caminho_pdf', type=str)
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument(
            '--incluir-match-por-nome', action='store_true',
            help='Também importa funcionários cuja matrícula do PDF não bate com nenhum '
                 'cadastro, mas cujo nome casa com exatamente um cadastro existente. '
                 'Por padrão esses casos ficam de fora, só reportados, até confirmação manual.'
        )

    def handle(self, *args, **options):
        caminho = options['caminho_pdf']
        dry_run = options['dry_run']
        incluir_match_por_nome = options['incluir_match_por_nome']

        try:
            linhas = self._extrair_linhas(caminho)
        except FileNotFoundError:
            raise CommandError(f'Arquivo não encontrado: {caminho}')

        funcionarios_pdf, avisos_parsing = self._parse_linhas(linhas)

        stats = {
            'admissoes_preenchidas': 0,
            'admissoes_divergentes': [],
            'ancoras_definidas': [],
            'ferias_criadas': 0,
            'ferias_atualizadas': 0,
            'nao_encontrados': [],
            'encontrados_por_nome': [],
            'pendentes_por_nome': [],
        }

        try:
            with transaction.atomic():
                for matricula, dados in funcionarios_pdf.items():
                    self._importar_funcionario(
                        matricula, dados, stats,
                        incluir_match_por_nome=incluir_match_por_nome,
                    )

                self._imprimir_relatorio(funcionarios_pdf, stats, avisos_parsing)

                if dry_run:
                    self.stdout.write(self.style.WARNING('\n--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()
        except DryRunRollback:
            pass

    # ---------- extração do PDF ----------

    def _extrair_linhas(self, caminho):
        reader = PdfReader(caminho)
        brutas = []
        for page in reader.pages:
            texto = page.extract_text()
            for linha in texto.split('\n'):
                linha = linha.strip()
                if not linha or linha.startswith(PREFIXOS_IGNORADOS):
                    continue
                brutas.append(linha)

        # Nomes longos às vezes quebram em duas linhas físicas no PDF (ex.: "FULANO DE
        # LISBOA" / "TAL04/03/2026 ..."). Uma linha de dado válida sempre tem pelo menos
        # 3 datas (admissão + período aquisitivo); linhas sem nenhuma data são fragmentos
        # de nome e devem ser grudadas na linha seguinte antes de tokenizar.
        linhas = []
        fragmento = None
        for linha in brutas:
            if fragmento is not None:
                linha = f'{fragmento} {linha}'
                fragmento = None
            if not any(is_date(tok) for tok in linha.split()):
                fragmento = linha
                continue
            linhas.append(NOME_COLADO_EM_DATA_RE.sub(r'\1 \2', linha))
        if fragmento is not None:
            linhas.append(NOME_COLADO_EM_DATA_RE.sub(r'\1 \2', fragmento))
        return linhas

    # ---------- parsing das linhas em estrutura por funcionário ----------

    def _parse_linhas(self, linhas):
        funcionarios = {}
        avisos = []
        atual = None

        for linha in linhas:
            tokens = linha.split()

            if not is_date(tokens[0]):
                matricula = tokens[0]
                idx = 1
                nome_tokens = []
                while idx < len(tokens) and not is_date(tokens[idx]):
                    nome_tokens.append(tokens[idx])
                    idx += 1
                if idx >= len(tokens):
                    avisos.append(f'linha sem data de admissão reconhecível: {linha}')
                    continue
                nome = ' '.join(nome_tokens)
                admissao = tokens[idx]
                idx += 1
                if matricula not in funcionarios:
                    funcionarios[matricula] = {'nome': nome, 'admissao': admissao, 'periodos': [], 'gozos': []}
                atual = matricula
            else:
                if atual is None:
                    avisos.append(f'linha de continuação sem funcionário atual: {linha}')
                    continue
                idx = 1

            if idx + 2 >= len(tokens) or not is_date(tokens[idx]) or tokens[idx + 1] != 'à' or not is_date(tokens[idx + 2]):
                avisos.append(f'período aquisitivo não reconhecido ({funcionarios.get(atual, {}).get("nome", "?")}): {linha}')
                continue

            aq_ini, aq_fim = tokens[idx], tokens[idx + 2]
            idx += 3
            funcionarios[atual]['periodos'].append((aq_ini, aq_fim))

            remainder = tokens[idx:]
            consumido = [False] * len(remainder)
            i = 0
            while i < len(remainder):
                if (i + 4 < len(remainder)
                        and is_date(remainder[i]) and remainder[i + 1] == 'a' and is_date(remainder[i + 2])
                        and is_num(remainder[i + 3]) and is_num(remainder[i + 4])):
                    funcionarios[atual]['gozos'].append(
                        (remainder[i], remainder[i + 2], remainder[i + 3], remainder[i + 4])
                    )
                    for k in range(i, i + 5):
                        consumido[k] = True
                    i += 5
                else:
                    i += 1

            sobra_com_data = [t for k, t in enumerate(remainder) if not consumido[k] and is_date(t)]
            if sobra_com_data:
                avisos.append(
                    f'não foi possível interpretar completamente um trecho de férias/afastamento '
                    f'({funcionarios[atual]["nome"]}, matrícula {atual}): "{linha}"'
                )
            if 'perda' in linha.lower():
                avisos.append(
                    f'possível perda de direito a férias mencionada no relatório '
                    f'({funcionarios[atual]["nome"]}, matrícula {atual}): "{linha}" — revisar manualmente'
                )

        return funcionarios, avisos

    # ---------- localizacao / importação por funcionário ----------

    def _localizar_funcionario(self, matricula, nome_pdf, stats, incluir_match_por_nome):
        try:
            return Funcionario.objects.get(matricula=matricula)
        except Funcionario.DoesNotExist:
            pass

        candidatos = list(Funcionario.objects.filter(nome__iexact=nome_pdf))
        if len(candidatos) == 1:
            if incluir_match_por_nome:
                stats['encontrados_por_nome'].append((matricula, nome_pdf, candidatos[0].matricula))
                return candidatos[0]
            stats['pendentes_por_nome'].append((matricula, nome_pdf, candidatos[0].matricula))
            return None

        stats['nao_encontrados'].append((matricula, nome_pdf, len(candidatos)))
        return None

    def _importar_funcionario(self, matricula, dados, stats, incluir_match_por_nome):
        funcionario = self._localizar_funcionario(matricula, dados['nome'], stats, incluir_match_por_nome)
        if funcionario is None:
            return

        admissao = parse_data(dados['admissao'])
        if funcionario.data_admissao is None:
            funcionario.data_admissao = admissao
            stats['admissoes_preenchidas'] += 1
        elif funcionario.data_admissao != admissao:
            stats['admissoes_divergentes'].append(
                (matricula, funcionario.nome, funcionario.data_admissao, admissao)
            )

        periodos_unicos = list(dict.fromkeys(dados['periodos']))
        if periodos_unicos and funcionario.data_base_ferias is None:
            ancora = calcular_ancora_periodos(periodos_unicos)
            if ancora is not None and ancora != admissao:
                funcionario.data_base_ferias = ancora
                stats['ancoras_definidas'].append((matricula, funcionario.nome, ancora))

        funcionario.save()

        for gozo_ini, _gozo_fim, dias, abono in dados['gozos']:
            _, criada = SolicitacaoFerias.objects.update_or_create(
                funcionario=funcionario,
                data_solicitada=parse_data(gozo_ini),
                defaults={
                    'dias_solicitados': parse_dias(dias),
                    'dias_abono': parse_dias(abono),
                    'status': 'aprovada',
                }
            )
            if criada:
                stats['ferias_criadas'] += 1
            else:
                stats['ferias_atualizadas'] += 1

    # ---------- relatório final ----------

    def _imprimir_relatorio(self, funcionarios_pdf, stats, avisos_parsing):
        self.stdout.write(self.style.SUCCESS(
            f'Funcionários lidos do PDF: {len(funcionarios_pdf)}\n'
            f'Admissões preenchidas (estavam vazias): {stats["admissoes_preenchidas"]}\n'
            f'Férias criadas: {stats["ferias_criadas"]}\n'
            f'Férias atualizadas: {stats["ferias_atualizadas"]}\n'
        ))

        if stats['ancoras_definidas']:
            self.stdout.write(self.style.WARNING(
                f'\nCiclo de férias ancorado no período mais antigo coberto pelo relatório, em vez da '
                f'admissão real ({len(stats["ancoras_definidas"])} funcionário(s)) — necessário porque o '
                f'relatório só traz períodos recentes; sem isso o cálculo de vencimento tentaria '
                f'reconstruir anos de férias não importados:'
            ))
            for matricula, nome, ancora in stats['ancoras_definidas']:
                self.stdout.write(f'  matrícula {matricula} — {nome}: ciclo calculado a partir de {ancora:%d/%m/%Y}')

        if stats['pendentes_por_nome']:
            self.stdout.write(self.style.WARNING(
                f'\nMatrícula do PDF não encontrada, mas há match único por nome — NADA importado '
                f'para eles ainda; rode de novo com --incluir-match-por-nome após confirmar '
                f'({len(stats["pendentes_por_nome"])}):'
            ))
            for matricula_pdf, nome, matricula_sistema in stats['pendentes_por_nome']:
                self.stdout.write(f'  PDF matrícula {matricula_pdf} ({nome}) -> cadastro matrícula {matricula_sistema!r}')

        if stats['admissoes_divergentes']:
            self.stdout.write(self.style.WARNING(
                f'\nData de admissão já cadastrada diverge da informada no PDF '
                f'({len(stats["admissoes_divergentes"])}) — não foi sobrescrita, revisar manualmente:'
            ))
            for matricula, nome, atual, pdf in stats['admissoes_divergentes']:
                self.stdout.write(f'  matrícula {matricula} — {nome}: sistema={atual:%d/%m/%Y} PDF={pdf:%d/%m/%Y}')

        if stats['encontrados_por_nome']:
            self.stdout.write(self.style.WARNING(
                f'\nMatrícula do PDF não encontrada, mas houve match único por nome '
                f'({len(stats["encontrados_por_nome"])}) — considere corrigir a matrícula cadastrada:'
            ))
            for matricula_pdf, nome, matricula_sistema in stats['encontrados_por_nome']:
                self.stdout.write(f'  PDF matrícula {matricula_pdf} ({nome}) -> cadastro matrícula {matricula_sistema!r}')

        if stats['nao_encontrados']:
            self.stdout.write(self.style.ERROR(
                f'\nNão encontrados no sistema ({len(stats["nao_encontrados"])}) — nenhum dado importado para eles:'
            ))
            for matricula, nome, n_candidatos in stats['nao_encontrados']:
                detalhe = 'nome ambíguo (múltiplos cadastros)' if n_candidatos > 1 else 'sem cadastro correspondente'
                self.stdout.write(f'  PDF matrícula {matricula} — {nome} ({detalhe})')

        if avisos_parsing:
            self.stdout.write(self.style.WARNING(
                f'\nAvisos de leitura do PDF ({len(avisos_parsing)}) — revisar manualmente:'
            ))
            for aviso in avisos_parsing:
                self.stdout.write(f'  {aviso}')
