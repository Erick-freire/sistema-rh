import tempfile

import openpyxl
from django.core.management import call_command
from django.test import TestCase

from .forms import PreCadastroFuncionarioForm
from .models import Funcionario, Setor


def _dados_cadastro(**kwargs):
    dados = dict(
        nome='Novo Funcionario', cpf='11122233396', pis='12345678901',
        cargo='Analista', data_admissao='2026-08-19',
    )
    dados.update(kwargs)
    return dados


class PreCadastroFuncionarioFormTests(TestCase):
    """PIS agora é obrigatório no pré-cadastro -- necessário pra bater ponto em relógios que identificam por PIS."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='RH')

    def test_pis_obrigatorio(self):
        form = PreCadastroFuncionarioForm(_dados_cadastro(pis='', setor=self.setor.id))

        self.assertFalse(form.is_valid())
        self.assertIn('pis', form.errors)

    def test_pis_precisa_ter_11_digitos(self):
        form = PreCadastroFuncionarioForm(_dados_cadastro(pis='123', setor=self.setor.id))

        self.assertFalse(form.is_valid())
        self.assertIn('pis', form.errors)

    def test_pis_duplicado_nao_permite(self):
        Funcionario.objects.create(nome='Já Existe', cpf='11111111111', pis='12345678901', cargo='X', setor=self.setor)

        form = PreCadastroFuncionarioForm(_dados_cadastro(cpf='22222222222', setor=self.setor.id))

        self.assertFalse(form.is_valid())
        self.assertIn('pis', form.errors)

    def test_cadastro_valido_salva_pis(self):
        form = PreCadastroFuncionarioForm(_dados_cadastro(pis='12345678901', setor=self.setor.id))

        self.assertTrue(form.is_valid(), form.errors)
        funcionario = form.save(commit=False)
        funcionario.setor = self.setor
        funcionario.status = 'pendente'
        funcionario.save()

        self.assertEqual(funcionario.pis, '12345678901')


class ImportarPisPlanilhaTests(TestCase):
    """Comando que casa a planilha de RH (Nome/CPF/PIS) por CPF pra preencher o PIS de quem já existe no sistema."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='RH')

    def _criar_planilha(self, linhas):
        arquivo = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Naves'
        ws.append(['Setor', 'Nome', 'CPF', 'PIS'])
        for linha in linhas:
            ws.append(linha)
        wb.save(arquivo.name)
        return arquivo.name

    def test_preenche_pis_de_quem_ainda_nao_tem(self):
        funcionario = Funcionario.objects.create(nome='Maria Silva', cpf='33333333333', cargo='X', setor=self.setor)
        planilha = self._criar_planilha([
            ['ADM', 'MARIA SILVA', '333.333.333-33', '123.45678.90-1'],
        ])

        call_command('importar_pis_planilha', planilha)

        funcionario.refresh_from_db()
        self.assertEqual(funcionario.pis, '12345678901')

    def test_nao_sobrescreve_pis_ja_cadastrado(self):
        funcionario = Funcionario.objects.create(
            nome='Maria Silva', cpf='33333333333', pis='99999999999', cargo='X', setor=self.setor,
        )
        planilha = self._criar_planilha([
            ['ADM', 'MARIA SILVA', '333.333.333-33', '123.45678.90-1'],
        ])

        call_command('importar_pis_planilha', planilha)

        funcionario.refresh_from_db()
        self.assertEqual(funcionario.pis, '99999999999')

    def test_ignora_pis_zerado_da_planilha(self):
        funcionario = Funcionario.objects.create(nome='Maria Silva', cpf='33333333333', cargo='X', setor=self.setor)
        planilha = self._criar_planilha([
            ['ADM', 'MARIA SILVA', '333.333.333-33', '000.00000.00.0'],
        ])

        call_command('importar_pis_planilha', planilha)

        funcionario.refresh_from_db()
        self.assertIsNone(funcionario.pis)

    def test_dry_run_nao_grava(self):
        funcionario = Funcionario.objects.create(nome='Maria Silva', cpf='33333333333', cargo='X', setor=self.setor)
        planilha = self._criar_planilha([
            ['ADM', 'MARIA SILVA', '333.333.333-33', '123.45678.90-1'],
        ])

        call_command('importar_pis_planilha', planilha, '--dry-run')

        funcionario.refresh_from_db()
        self.assertIsNone(funcionario.pis)

    def test_nao_grava_pis_que_ja_pertence_a_outro_funcionario(self):
        Funcionario.objects.create(nome='Outra Pessoa', cpf='44444444444', pis='12345678901', cargo='X', setor=self.setor)
        funcionario = Funcionario.objects.create(nome='Maria Silva', cpf='33333333333', cargo='X', setor=self.setor)
        planilha = self._criar_planilha([
            ['ADM', 'MARIA SILVA', '333.333.333-33', '123.45678.90-1'],
        ])

        call_command('importar_pis_planilha', planilha)

        funcionario.refresh_from_db()
        self.assertIsNone(funcionario.pis)

    def test_ignora_cpf_que_nao_existe_no_rh(self):
        planilha = self._criar_planilha([
            ['ADM', 'FULANO DE TAL', '555.555.555-55', '123.45678.90-1'],
        ])

        # Não deve levantar exceção mesmo sem nenhum funcionário correspondente.
        call_command('importar_pis_planilha', planilha)

        self.assertFalse(Funcionario.objects.filter(pis='12345678901').exists())
