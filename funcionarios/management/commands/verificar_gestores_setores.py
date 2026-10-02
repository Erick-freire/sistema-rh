import openpyxl
from django.core.management.base import BaseCommand, CommandError

from funcionarios.models import Funcionario, Setor


class Command(BaseCommand):
    help = (
        'Compara uma planilha Excel (aba "Naves", mesma estrutura da importação original) com o '
        'banco atual e APENAS RELATA as diferenças de funcionários, setores e gestores. '
        'Não grava, não cria e não atualiza nada.'
    )

    def add_arguments(self, parser):
        parser.add_argument('caminho_planilha', type=str)

    def handle(self, *args, **options):
        caminho = options['caminho_planilha']

        try:
            wb = openpyxl.load_workbook(caminho, data_only=True)
        except FileNotFoundError:
            raise CommandError(f'Planilha não encontrada: {caminho}')

        if 'Naves' not in wb.sheetnames:
            raise CommandError(
                f'A planilha não tem a aba "Naves". Abas encontradas: {", ".join(wb.sheetnames)}'
            )

        ws = wb['Naves']
        headers = [c.value for c in ws[1]]
        idx = {h: i for i, h in enumerate(headers)}

        for coluna in ('Nome', 'CPF', 'Matrícula eSocial', 'Setor', 'Responsável do Setor'):
            if coluna not in idx:
                raise CommandError(f'A aba "Naves" não tem a coluna obrigatória "{coluna}".')

        nao_encontrados_por_matricula = []
        pares_setor_responsavel = {}
        linhas_lidas = 0

        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row[idx['Nome']]:
                continue

            linhas_lidas += 1

            nome = str(row[idx['Nome']]).strip()
            cpf = self._limpar_cpf(row[idx['CPF']])
            matricula = str(row[idx['Matrícula eSocial']] or '').strip()
            setor_nome = str(row[idx['Setor']] or '').strip()
            responsavel = str(row[idx['Responsável do Setor']] or '').strip()

            if not Funcionario.objects.filter(matricula=matricula).exists():
                nao_encontrados_por_matricula.append({
                    'matricula': matricula,
                    'nome': nome,
                    'cpf': cpf,
                    'setor': setor_nome,
                })

            if setor_nome and responsavel:
                chave = (setor_nome.upper(), responsavel.upper())
                pares_setor_responsavel.setdefault(chave, (setor_nome, responsavel))

        responsaveis_nao_encontrados = {}
        setores_novos = []
        mudancas_de_gestor = []
        setores_sem_mudanca = 0
        nomes_ambiguos = {}

        for setor_nome, responsavel in pares_setor_responsavel.values():
            candidatos = list(Funcionario.objects.filter(nome__iexact=responsavel).order_by('pk'))

            if not candidatos:
                responsaveis_nao_encontrados.setdefault(responsavel, []).append(setor_nome)
                continue

            if len(candidatos) > 1:
                nomes_ambiguos.setdefault(responsavel, candidatos)

            gestor = candidatos[0]
            setor = Setor.objects.filter(nome__iexact=setor_nome).order_by('pk').first()

            if setor is None:
                setores_novos.append({
                    'setor': setor_nome,
                    'gestor_proposto': gestor,
                })
            elif setor.gestor_id != gestor.pk:
                mudancas_de_gestor.append({
                    'setor': setor,
                    'gestor_atual': setor.gestor,
                    'gestor_proposto': gestor,
                })
            else:
                setores_sem_mudanca += 1

        self._relatar(
            linhas_lidas,
            nao_encontrados_por_matricula,
            responsaveis_nao_encontrados,
            setores_novos,
            mudancas_de_gestor,
            setores_sem_mudanca,
            nomes_ambiguos,
        )

    def _relatar(self, linhas_lidas, nao_encontrados_por_matricula, responsaveis_nao_encontrados,
                 setores_novos, mudancas_de_gestor, setores_sem_mudanca, nomes_ambiguos):
        escrever = self.stdout.write

        escrever('')
        escrever(f'Linhas da aba "Naves" com Nome preenchido: {linhas_lidas}')
        escrever('')

        escrever(self.style.MIGRATE_HEADING(
            '=== FUNCIONÁRIOS DA PLANILHA NÃO ENCONTRADOS NO BANCO (por matrícula) ==='
        ))
        if nao_encontrados_por_matricula:
            for item in nao_encontrados_por_matricula:
                escrever(
                    '  - matrícula {} | {} | CPF {} | setor {}'.format(
                        item['matricula'] or '(vazia)',
                        item['nome'],
                        item['cpf'] or '(vazio)',
                        item['setor'] or '(vazio)',
                    )
                )
        else:
            escrever('  (nenhum)')
        escrever(f'  Total: {len(nao_encontrados_por_matricula)}')
        escrever('')

        escrever(self.style.MIGRATE_HEADING(
            '=== RESPONSÁVEIS DE SETOR NÃO ENCONTRADOS NO BANCO (por nome) ==='
        ))
        if responsaveis_nao_encontrados:
            for responsavel, setores in responsaveis_nao_encontrados.items():
                escrever('  - {} -> deveria gerenciar: {}'.format(responsavel, ', '.join(setores)))
        else:
            escrever('  (nenhum)')
        escrever(f'  Total: {len(responsaveis_nao_encontrados)}')
        escrever('')

        escrever(self.style.MIGRATE_HEADING(
            '=== SETORES NOVOS (não existem ainda no banco) ==='
        ))
        if setores_novos:
            for item in setores_novos:
                gestor = item['gestor_proposto']
                escrever(
                    '  - {} -> responsável proposto: {}'.format(item['setor'], self._descrever(gestor))
                )
        else:
            escrever('  (nenhum)')
        escrever(f'  Total: {len(setores_novos)}')
        escrever('')

        escrever(self.style.MIGRATE_HEADING(
            '=== MUDANÇAS DE GESTOR PROPOSTAS (setor já existe, gestor mudou) ==='
        ))
        mesmo_nome = 0
        if mudancas_de_gestor:
            for item in mudancas_de_gestor:
                atual = item['gestor_atual']
                novo = item['gestor_proposto']
                atual_txt = self._descrever(atual) if atual else '(sem gestor)'
                linha = '  - {}: {} -> {}'.format(
                    item['setor'].nome, atual_txt, self._descrever(novo)
                )
                if atual and atual.nome.strip().upper() == novo.nome.strip().upper():
                    mesmo_nome += 1
                    linha += '  [MESMO NOME - cadastro duplicado no banco, nao e troca real de gestor]'
                escrever(linha)
        else:
            escrever('  (nenhuma)')
        escrever(f'  Total: {len(mudancas_de_gestor)}')
        if mesmo_nome:
            escrever(self.style.WARNING(
                '  Destes, {} sao apenas homonimos/duplicatas de cadastro (mesmo nome, registro '
                'diferente) e {} sao troca real de pessoa.'.format(
                    mesmo_nome, len(mudancas_de_gestor) - mesmo_nome
                )
            ))
        escrever('')

        escrever(self.style.MIGRATE_HEADING(
            '=== SETORES SEM NENHUMA MUDANÇA (gestor já está correto) ==='
        ))
        escrever(f'  Total: {setores_sem_mudanca}')
        escrever('')

        if nomes_ambiguos:
            escrever(self.style.ERROR(
                '=== ATENÇÃO: NOMES DE RESPONSÁVEL COM MAIS DE UM CADASTRO NO BANCO ==='
            ))
            escrever('  (a busca por nome fica ambígua nesses casos; o relatório usou o de menor pk)')
            for nome, candidatos in nomes_ambiguos.items():
                escrever(f'  - {nome}:')
                for funcionario in candidatos:
                    escrever(
                        '      pk {} | matrícula {} | cargo {} | status {}'.format(
                            funcionario.pk, funcionario.matricula,
                            funcionario.cargo, funcionario.status,
                        )
                    )
            escrever(f'  Total de nomes ambíguos: {len(nomes_ambiguos)}')
            escrever('')

        escrever(self.style.WARNING(
            'Comando somente-leitura: nada foi criado, alterado ou salvo no banco.'
        ))

    def _descrever(self, funcionario):
        return '{} (pk {}, matrícula {})'.format(
            funcionario.nome, funcionario.pk, funcionario.matricula
        )

    def _limpar_cpf(self, cpf_bruto):
        return ''.join(filter(str.isdigit, str(cpf_bruto or '')))
