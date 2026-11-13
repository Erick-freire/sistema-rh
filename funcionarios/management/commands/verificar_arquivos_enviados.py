"""
Procura na pasta de mídia arquivo que não seja PDF, JPG nem PNG de verdade.

Só lê: não apaga, não move e não altera nada. Serve para conferir se entrou
algum arquivo suspeito (HTML, SVG, script) antes de o sistema validar os
uploads. Esses arquivos já saem como download e não executam
(`funcionarios/arquivos.py`), mas vale saber quais são e de onde vieram.

Uso:
    python manage.py verificar_arquivos_enviados
    python manage.py verificar_arquivos_enviados --pasta terceiros_documentos --limite 50
"""
import os
import re
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from funcionarios.arquivos import BYTES_INSPECIONADOS, tipo_pelo_conteudo

# Marcas de conteúdo que o navegador executaria se abrisse como página.
MARCAS_DE_PAGINA = re.compile(
    rb'<\s*(html|script|svg|iframe|object|embed|body|head|meta)\b|<!doctype|<\?xml|javascript:',
    re.IGNORECASE,
)

PERIGOSO = 'PÁGINA/SCRIPT (HTML, SVG ou XML)'
DESCONHECIDO = 'formato não reconhecido'


def classificar(inicio):
    """None para PDF, JPG ou PNG; senão, a descrição do que parece ser."""
    if tipo_pelo_conteudo(inicio):
        return None
    if MARCAS_DE_PAGINA.search(inicio):
        return PERIGOSO
    return DESCONHECIDO


class Command(BaseCommand):
    help = 'Lista (só leitura) os arquivos da mídia cujo conteúdo não é PDF, JPG nem PNG.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--pasta', default='',
            help='Subpasta da mídia a varrer (ex.: terceiros_documentos). Padrão: a mídia inteira.',
        )
        parser.add_argument(
            '--limite', type=int, default=200,
            help='Quantos arquivos listar no máximo (os de página/script vêm primeiro). Padrão: 200.',
        )

    def handle(self, *args, **opcoes):
        raiz = os.path.realpath(settings.MEDIA_ROOT)
        alvo = os.path.realpath(os.path.join(raiz, opcoes['pasta']))
        if os.path.commonpath([raiz, alvo]) != raiz:
            raise CommandError('A pasta precisa estar dentro da mídia.')
        if not os.path.isdir(alvo):
            raise CommandError(f'Pasta não encontrada: {alvo}')

        total_por_pasta = Counter()
        suspeitos_por_pasta = Counter()
        suspeitos = []

        for diretorio, _, nomes in os.walk(alvo):
            for nome in nomes:
                caminho = os.path.join(diretorio, nome)
                relativo = os.path.relpath(caminho, raiz).replace(os.sep, '/')
                pasta = relativo.split('/', 1)[0] if '/' in relativo else '(raiz)'
                total_por_pasta[pasta] += 1
                try:
                    with open(caminho, 'rb') as arquivo:
                        inicio = arquivo.read(BYTES_INSPECIONADOS)
                except OSError as erro:
                    self.stderr.write(f'Não foi possível ler {relativo}: {erro}')
                    continue
                classe = classificar(inicio)
                if classe:
                    suspeitos_por_pasta[pasta] += 1
                    suspeitos.append((classe, relativo, os.path.getsize(caminho)))

        total = sum(total_por_pasta.values())
        self.stdout.write(f'Mídia: {raiz}')
        self.stdout.write(f'{total} arquivo(s) verificado(s), {len(suspeitos)} fora de PDF/JPG/PNG.\n')

        self.stdout.write('Por pasta (verificados / fora do padrão):')
        for pasta in sorted(total_por_pasta):
            self.stdout.write(f'  {pasta}: {total_por_pasta[pasta]} / {suspeitos_por_pasta[pasta]}')

        perigosos = [s for s in suspeitos if s[0] == PERIGOSO]
        self.stdout.write(f'\n{len(perigosos)} com conteúdo de página/script (HTML, SVG ou XML).')
        if not suspeitos:
            return

        suspeitos.sort(key=lambda s: (s[0] != PERIGOSO, s[1]))
        limite = max(opcoes['limite'], 0)
        self.stdout.write(f'\nArquivos (até {limite}):')
        for classe, relativo, tamanho in suspeitos[:limite]:
            self.stdout.write(f'  [{classe}] {relativo} ({tamanho} bytes)')
        if len(suspeitos) > limite:
            self.stdout.write(f'  ... e mais {len(suspeitos) - limite}. Use --limite para ver mais.')
