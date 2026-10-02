import openpyxl
from django.core.management.base import BaseCommand
from django.db import transaction
from funcionarios.models import Setor, Funcionario
from datetime import datetime


class DryRunRollback(Exception):
    """Usada só para forçar rollback quando --dry-run está ativo."""
    pass


class Command(BaseCommand):
    help = 'Importa funcionários e setores a partir de uma planilha Excel (aba "Naves")'

    def add_arguments(self, parser):
        parser.add_argument('caminho_arquivo', type=str)
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simula a importação sem gravar nada no banco',
        )

    def handle(self, *args, **options):
        caminho = options['caminho_arquivo']
        dry_run = options['dry_run']

        wb = openpyxl.load_workbook(caminho, data_only=True)
        ws = wb['Naves']

        headers = [c.value for c in ws[1]]
        idx = {h: i for i, h in enumerate(headers)}

        setores_criados = 0
        funcionarios_criados = 0
        funcionarios_atualizados = 0
        gestores_placeholder = 0
        gestores_reaproveitados = 0

        try:
            with transaction.atomic():
                nome_para_funcionario = {}
                responsaveis_por_setor = {}

                for row in ws.iter_rows(min_row=2, values_only=True):
                    if not row[idx['Nome']]:
                        continue

                    nome = row[idx['Nome']].strip()
                    cpf = self._limpar_cpf(row[idx['CPF']])
                    matricula = str(row[idx['Matrícula eSocial']])
                    cargo = row[idx['Função']] or 'Não informado'
                    setor_nome = row[idx['Setor']].strip()
                    endereco = self._montar_endereco(row, idx)
                    telefone = self._montar_telefone(row, idx)
                    data_admissao = self._parse_data_admissao(row[idx['Data de Admissão']])

                    setor, criado = Setor.objects.get_or_create(nome=setor_nome)
                    if criado:
                        setores_criados += 1

                    funcionario, criado = Funcionario.objects.update_or_create(
                        matricula=matricula,
                        defaults={
                            'nome': nome,
                            'cpf': cpf,
                            'cargo': cargo,
                            'setor': setor,
                            'status': 'ativo',
                            'telefone': telefone,
                            'endereco': endereco,
                            'data_admissao': data_admissao,
                        }
                    )
                    if criado:
                        funcionarios_criados += 1
                    else:
                        funcionarios_atualizados += 1

                    nome_para_funcionario[nome.upper()] = funcionario
                    responsaveis_por_setor[setor] = row[idx['Responsável do Setor']].strip()

                placeholders_criados = {}

                for setor, nome_responsavel in responsaveis_por_setor.items():
                    chave = nome_responsavel.upper()
                    gestor = nome_para_funcionario.get(chave)

                    if gestor is None:
                        gestor = placeholders_criados.get(chave)

                    if gestor is None:
                        gestor, criado_agora = self._criar_gestor_placeholder(nome_responsavel)
                        placeholders_criados[chave] = gestor
                        if criado_agora:
                            gestores_placeholder += 1
                        else:
                            gestores_reaproveitados += 1

                    setor.gestor = gestor
                    setor.save()

                self.stdout.write(self.style.SUCCESS(
                    f'Setores criados: {setores_criados}\n'
                    f'Funcionários criados: {funcionarios_criados}\n'
                    f'Funcionários atualizados: {funcionarios_atualizados}\n'
                    f'Gestores criados como placeholder: {gestores_placeholder}\n'
                    f'Gestores placeholder reaproveitados (já existiam): {gestores_reaproveitados}'
                ))

                if dry_run:
                    self.stdout.write(self.style.WARNING('--dry-run ativo: nada foi gravado de fato.'))
                    raise DryRunRollback()

        except DryRunRollback:
            pass

    def _limpar_cpf(self, cpf_bruto):
        return ''.join(filter(str.isdigit, str(cpf_bruto or '')))

    def _montar_endereco(self, row, idx):
        partes = [
            row[idx['Endereço']],
            row[idx['Número']],
            row[idx['Bairro']],
            row[idx['Município']],
            row[idx['Estado']],
        ]
        return ', '.join(str(p) for p in partes if p)
    
    def _parse_data_admissao(self, valor):
        if valor is None or valor == '':
            return None

        if hasattr(valor, 'date'):
            return valor.date()

        if isinstance(valor, str):
            try:
                return datetime.strptime(valor.strip(), '%d/%m/%Y').date()
            except ValueError:
                return None

        return valor

    def _montar_telefone(self, row, idx):
        if row[idx['DDD Celular']] and row[idx['Celular']]:
            return f"({row[idx['DDD Celular']]}) {row[idx['Celular']]}"
        if row[idx['DDD']] and row[idx['Fone']]:
            return f"({row[idx['DDD']]}) {row[idx['Fone']]}"
        return ''

    def _criar_gestor_placeholder(self, nome):
        nome = nome.strip()

        # Reaproveita um placeholder já existente com esse nome -- sem isso, rodar a
        # importação duas vezes cria um segundo placeholder para a mesma pessoa.
        existente = Funcionario.objects.filter(
            nome__iexact=nome,
            cargo='Gestor (cadastro pendente)',
        ).order_by('pk').first()
        if existente:
            return existente, False

        contador = Funcionario.objects.filter(cargo='Gestor (cadastro pendente)').count() + 1
        while Funcionario.objects.filter(matricula=f'PENDENTE-{contador}').exists():
            contador += 1

        novo = Funcionario.objects.create(
            nome=nome,
            cpf=f'{contador:011d}',
            matricula=f'PENDENTE-{contador}',
            cargo='Gestor (cadastro pendente)',
            setor=Setor.objects.get_or_create(nome='A definir')[0],
            status='ativo',
        )
        return novo, True