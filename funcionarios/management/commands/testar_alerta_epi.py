"""
Comando de teste da tela "EPIs Vencendo" (itens entregues + validade do CA).

Coloque este arquivo em:
    funcionarios/management/commands/testar_alerta_epi.py

Uso:
    python manage.py testar_alerta_epi                    # cria os dados de teste
    python manage.py testar_alerta_epi --cpf 12345678900  # escolhe o funcionário
    python manage.py testar_alerta_epi --limpar           # remove tudo que foi criado

As datas são calculadas a partir de HOJE, então o teste funciona em qualquer dia.

Como o que é de teste é identificado (e só por isso o --limpar o encontra):
  - itens do catálogo: nome começando com "[TESTE]"
  - ficha de entrega:  observacoes_devolucao igual a "[TESTE ALERTA EPI]"
Dados reais nunca têm esses marcadores, então a limpeza não toca neles.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import ProtectedError
from django.utils import timezone

from funcionarios.models import Funcionario, ItemCatalogoEPI, FichaEPI, ItemEPI

MARCADOR_FICHA = '[TESTE ALERTA EPI]'
PREFIXO_CATALOGO = '[TESTE]'

# Itens do catálogo de teste: (chave, nome, número do CA, dias a partir de hoje ou None, o que esperar)
CATALOGO_TESTE = [
    ('vencido', 'CA vencido há 15 dias', 'TESTE-A', -15,
     'Bloco "CAs vencidos ou vencendo": DEVE APARECER como vencido, com 1 em uso'),
    ('vencendo', 'CA vence em 20 dias', 'TESTE-B', 20,
     'Bloco "CAs vencidos ou vencendo": DEVE APARECER (dentro dos 60 dias), com 0 em uso'),
    ('valido', 'CA válido por 1 ano', 'TESTE-C', 365,
     'NÃO deve aparecer em bloco de CA (só serve de base para os itens entregues)'),
    ('sem_validade', 'CA sem validade cadastrada', 'TESTE-D', None,
     'Bloco "CAs sem validade cadastrada": DEVE APARECER'),
]

# Itens entregues (bloco 1): (descrição, dias a partir de hoje, já devolvido?, o que esperar)
ITENS_ENTREGUES = [
    ('Vencido há 10 dias', -10, False, 'DEVE APARECER (vencido)'),
    ('Vence em 5 dias', 5, False, 'DEVE APARECER (dentro do prazo de alerta de 30 dias)'),
    ('Vence em 90 dias', 90, False, 'NÃO deve aparecer (fora do prazo de alerta)'),
    ('Vencido há 10 dias, já devolvido', -10, True, 'NÃO deve aparecer (item devolvido)'),
]


class Command(BaseCommand):
    help = 'Cria (ou remove, com --limpar) dados de teste para a tela EPIs Vencendo.'

    def add_arguments(self, parser):
        parser.add_argument('--cpf', help='CPF (somente números) do funcionário de teste. Padrão: o primeiro funcionário ativo.')
        parser.add_argument('--limpar', action='store_true', help='Remove os dados de teste criados por este comando.')

    def handle(self, *args, **options):
        if options['limpar']:
            self._limpar()
            return

        if options['cpf']:
            funcionario = Funcionario.objects.filter(cpf=options['cpf']).first()
        else:
            funcionario = Funcionario.objects.filter(status='ativo').first()
        if funcionario is None:
            raise CommandError('Funcionário não encontrado.')

        hoje = timezone.now().date()

        # 1) Catálogo de teste (cada item com uma situação de validade do CA)
        catalogo = {}
        for chave, nome, numero_ca, dias, _ in CATALOGO_TESTE:
            validade = hoje + timedelta(days=dias) if dias is not None else None
            catalogo[chave] = ItemCatalogoEPI.objects.create(
                nome=f'{PREFIXO_CATALOGO} {nome}',
                numero_ca=numero_ca,
                validade_ca=validade,
                ativo=True,
            )

        # 2) Ficha de entrega de teste
        ficha = FichaEPI.objects.create(
            funcionario=funcionario,
            data_entrega=hoje,
            status='ativo',
            observacoes_devolucao=MARCADOR_FICHA,
        )

        # Itens do CA vencido: um em uso e um já devolvido (a contagem "em uso" deve dar 1)
        ItemEPI.objects.create(ficha=ficha, item_catalogo=catalogo['vencido'], quantidade=1, devolvido=False)
        ItemEPI.objects.create(ficha=ficha, item_catalogo=catalogo['vencido'], quantidade=1, devolvido=True)

        # Itens entregues com datas de vencimento variadas (usam o CA válido, para não misturar os blocos)
        for descricao, dias, devolvido, _ in ITENS_ENTREGUES:
            ItemEPI.objects.create(
                ficha=ficha,
                item_catalogo=catalogo['valido'],
                quantidade=1,
                devolvido=devolvido,
                data_vencimento=hoje + timedelta(days=dias),
            )

        self.stdout.write(f'Dados de teste criados para: {funcionario.nome}\n')

        self.stdout.write(self.style.MIGRATE_HEADING('Bloco 1 — itens entregues:'))
        for descricao, dias, _, esperado in ITENS_ENTREGUES:
            vencimento = hoje + timedelta(days=dias)
            self.stdout.write(f'  vence {vencimento:%d/%m/%Y} | {descricao} -> {esperado}')

        self.stdout.write(self.style.MIGRATE_HEADING('\nBlocos 2 e 3 — validade do CA no catálogo:'))
        for _, nome, _, dias, esperado in CATALOGO_TESTE:
            validade = f'{hoje + timedelta(days=dias):%d/%m/%Y}' if dias is not None else 'sem data   '
            self.stdout.write(f'  {validade} | {nome} -> {esperado}')

        self.stdout.write(self.style.WARNING(
            '\nAbra a tela "EPIs Vencendo" com um usuário de papel seguranca_trabalho ou rh.'
            '\nQuando terminar: python manage.py testar_alerta_epi --limpar'
        ))

    def _limpar(self):
        # Ordem importa: o item do catálogo é protegido (PROTECT) enquanto houver item de ficha usando.
        fichas = FichaEPI.objects.filter(observacoes_devolucao=MARCADOR_FICHA)
        total_fichas = fichas.count()
        fichas.delete()  # os itens da ficha saem junto (CASCADE)

        catalogo = ItemCatalogoEPI.objects.filter(nome__startswith=PREFIXO_CATALOGO)
        total_catalogo = catalogo.count()
        try:
            catalogo.delete()
        except ProtectedError:
            raise CommandError(
                'Algum item de catálogo de teste está em uso por uma ficha real. '
                'Remova essa ficha (ou troque o item) e rode --limpar de novo.'
            )

        self.stdout.write(self.style.SUCCESS(
            f'Removidos: {total_fichas} ficha(s) e {total_catalogo} item(ns) de catálogo de teste.'
        ))