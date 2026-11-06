import io
import tempfile

import openpyxl
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse

from datetime import date, datetime
from decimal import Decimal

from .forms import AtestadoForm, MeuPerfilForm, PreCadastroFuncionarioForm
from .models import (
    Atestado, Documento, FichaEPI, Funcionario, ItemCatalogoComodato, ItemComodato, Setor, SolicitacaoSenha,
    TermoComodato, TipoDocumento, Usuario,
)
from . import views


def _dados_cadastro(**kwargs):
    dados = dict(
        nome='Novo Funcionario', cpf='11122233396',
        cargo='Analista', data_admissao='2026-08-19',
    )
    dados.update(kwargs)
    return dados


def _dados_perfil(**kwargs):
    dados = dict(
        pis='12345678901', sexo='masculino', estado_civil='solteiro',
        email='colaborador@sotrigo.com.br', banco='Banco X', agencia='0001', numero_conta='12345',
        usa_vale_transporte='True',
    )
    dados.update(kwargs)
    return dados


class PreCadastroFuncionarioFormTests(TestCase):
    """PIS não faz mais parte do pré-cadastro -- passa a ser informado pelo próprio funcionário ao completar o cadastro."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='RH')

    def test_pis_nao_faz_parte_do_formulario(self):
        form = PreCadastroFuncionarioForm(_dados_cadastro(setor=self.setor.id))

        self.assertNotIn('pis', form.fields)

    def test_cadastro_valido_sem_pis(self):
        form = PreCadastroFuncionarioForm(_dados_cadastro(setor=self.setor.id))

        self.assertTrue(form.is_valid(), form.errors)
        funcionario = form.save(commit=False)
        funcionario.setor = self.setor
        funcionario.status = 'pendente_documentos'
        funcionario.save()

        self.assertIsNone(funcionario.pis)


class MeuPerfilFormPisTests(TestCase):
    """PIS é informado pelo próprio funcionário em Meu Perfil (não mais um documento), uma vez só: depois trava."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='RH')
        self.funcionario = Funcionario.objects.create(
            nome='Colaborador Teste', cpf='11122233396', cargo='Analista', setor=self.setor,
        )

    def test_pis_obrigatorio(self):
        form = MeuPerfilForm(_dados_perfil(pis=''), instance=self.funcionario)

        self.assertFalse(form.is_valid())
        self.assertIn('pis', form.errors)

    def test_pis_precisa_ter_11_digitos(self):
        form = MeuPerfilForm(_dados_perfil(pis='123'), instance=self.funcionario)

        self.assertFalse(form.is_valid())
        self.assertIn('pis', form.errors)

    def test_pis_duplicado_nao_permite(self):
        Funcionario.objects.create(nome='Já Existe', cpf='11111111111', pis='12345678901', cargo='X', setor=self.setor)

        form = MeuPerfilForm(_dados_perfil(pis='12345678901'), instance=self.funcionario)

        self.assertFalse(form.is_valid())
        self.assertIn('pis', form.errors)

    def test_perfil_valido_salva_pis(self):
        form = MeuPerfilForm(_dados_perfil(pis='12345678901'), instance=self.funcionario)

        self.assertTrue(form.is_valid(), form.errors)
        funcionario = form.save()

        self.assertEqual(funcionario.pis, '12345678901')

    def test_pis_ja_informado_nao_muda(self):
        self.funcionario.pis = '12345678901'
        self.funcionario.save()

        form = MeuPerfilForm(_dados_perfil(pis='99999999999'), instance=self.funcionario)

        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.funcionario.refresh_from_db()
        self.assertEqual(self.funcionario.pis, '12345678901')

    def test_pis_recem_digitado_nao_trava_se_outro_campo_tiver_erro(self):
        user = User.objects.create_user(username='colab', password='senha123')
        Usuario.objects.create(user=user, funcionario=self.funcionario, papel='colaborador')
        self.client.login(username='colab', password='senha123')

        resposta = self.client.post(reverse('meu_perfil'), _dados_perfil(email=''))

        self.assertContains(resposta, 'name="pis"')
        self.funcionario.refresh_from_db()
        self.assertIsNone(self.funcionario.pis)


class MeuPerfilValeTransporteTests(TestCase):
    """O próprio colaborador responde em Meu Perfil se usa vale-transporte (quem não usa não tem desconto na folha)."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='Produção')
        self.funcionario = Funcionario.objects.create(
            nome='Colaborador Teste', cpf='11122233396', cargo='Analista', setor=self.setor,
        )

    def test_resposta_e_obrigatoria(self):
        form = MeuPerfilForm(_dados_perfil(usa_vale_transporte=''), instance=self.funcionario)

        self.assertFalse(form.is_valid())
        self.assertIn('usa_vale_transporte', form.errors)

    def test_nao_usar_fica_gravado_como_resposta(self):
        form = MeuPerfilForm(_dados_perfil(usa_vale_transporte='False'), instance=self.funcionario)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertIs(form.save().usa_vale_transporte, False)

    def test_painel_pede_a_resposta_ate_ser_dada(self):
        user = User.objects.create_user(username='colab', password='senha123')
        Usuario.objects.create(user=user, funcionario=self.funcionario, papel='colaborador')
        self.client.login(username='colab', password='senha123')

        self.assertContains(self.client.get(reverse('dashboard')), 'Informar se vai usar o vale-transporte')

        self.funcionario.usa_vale_transporte = False
        self.funcionario.save()
        self.assertNotContains(self.client.get(reverse('dashboard')), 'Informar se vai usar o vale-transporte')


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


class ImportarFuncionariosTests(TestCase):
    """Importação da aba "Naves": demitido fica inativo e gestor já definido não é trocado pelo nome truncado."""

    COLUNAS = [
        'Setor', 'Responsável do Setor', 'Nome', 'CPF', 'Matrícula eSocial', 'Função',
        'Data de Admissão', 'Data de Demissão', 'Endereço', 'Número', 'Bairro', 'Município', 'Estado',
        'DDD', 'Fone', 'DDD Celular', 'Celular',
    ]

    def _linha(self, nome, cpf, matricula, setor='MOINHO', responsavel='JERUSA FERREIRA', demissao=None):
        return [
            setor, responsavel, nome, cpf, matricula, 'Operador',
            '01/02/2024', demissao, 'Rua A', '10', 'Centro', 'Brasília', 'DF',
            None, None, '61', '999990000',
        ]

    def _criar_planilha(self, linhas, colunas=None):
        arquivo = tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = 'Naves'
        ws.append(colunas or self.COLUNAS)
        for linha in linhas:
            ws.append(linha)
        wb.save(arquivo.name)
        return arquivo.name

    def _importar(self, linhas):
        call_command('importar_funcionarios', self._criar_planilha(linhas), stdout=io.StringIO())

    def _jerusa(self):
        return self._linha('JERUSA FERREIRA DE ALMEIDA SANTOS', '79500000000', '795')

    def test_demitido_fica_inativo(self):
        self._importar([
            self._jerusa(),
            self._linha('ANA SAIU', '11100000000', '1001', demissao=datetime(2026, 8, 3)),
        ])

        self.assertEqual(Funcionario.objects.get(matricula='1001').status, 'inativo')
        self.assertEqual(Funcionario.objects.get(matricula='795').status, 'ativo')

    def test_demissao_futura_continua_ativo(self):
        self._importar([
            self._jerusa(),
            self._linha('BIA AVISO', '22200000000', '1002', demissao=datetime(2099, 1, 31)),
        ])

        self.assertEqual(Funcionario.objects.get(matricula='1002').status, 'ativo')

    def test_sem_coluna_de_demissao_nao_importa(self):
        colunas = [c for c in self.COLUNAS if c != 'Data de Demissão']
        linha = self._jerusa()
        del linha[self.COLUNAS.index('Data de Demissão')]

        with self.assertRaises(CommandError):
            call_command('importar_funcionarios', self._criar_planilha([linha], colunas), stdout=io.StringIO())

        self.assertFalse(Funcionario.objects.exists())

    def test_nome_truncado_nao_troca_gestor_ja_definido(self):
        setor = Setor.objects.create(nome='MOINHO')
        jerusa = Funcionario.objects.create(
            nome='JERUSA FERREIRA DE ALMEIDA SANTOS', cpf='79500000000', matricula='795', cargo='Gerente', setor=setor,
        )
        setor.gestor = jerusa
        setor.save()

        self._importar([self._jerusa(), self._linha('CAIO OPERADOR', '33300000000', '1003')])

        setor.refresh_from_db()
        self.assertEqual(setor.gestor, jerusa)
        self.assertFalse(Funcionario.objects.filter(cargo='Gestor (cadastro pendente)').exists())

    def test_setor_novo_casa_nome_truncado_pela_matricula(self):
        self._importar([self._jerusa(), self._linha('CAIO OPERADOR', '33300000000', '1003')])

        self.assertEqual(Setor.objects.get(nome='MOINHO').gestor.matricula, '795')
        self.assertFalse(Funcionario.objects.filter(cargo='Gestor (cadastro pendente)').exists())


class OnboardingDocumentosTests(TestCase):
    def setUp(self):
        self.setor = Setor.objects.create(nome='Produção')
        self.tipo_doc = TipoDocumento.objects.create(nome='RG', obrigatorio=True)

        self.funcionario = Funcionario.objects.create(
            nome='Colaborador Teste', cpf='12345678901', cargo='Analista', setor=self.setor,
            status='pendente_documentos', endereco='Rua Teste, 1', salario='2000.00',
        )
        self.user_colaborador = User.objects.create_user(username='12345678901', password='senha123')
        Usuario.objects.create(user=self.user_colaborador, funcionario=self.funcionario, papel='colaborador')

        funcionario_rh = Funcionario.objects.create(
            nome='RH Teste', cpf='98765432100', cargo='RH', setor=self.setor, status='ativo',
        )
        self.user_rh = User.objects.create_user(username='98765432100', password='senha123')
        Usuario.objects.create(user=self.user_rh, funcionario=funcionario_rh, papel='rh')

    def _arquivo(self, nome='arquivo.pdf'):
        return SimpleUploadedFile(nome, b'conteudo', content_type='application/pdf')

    def test_envio_do_ultimo_documento_inicial_avanca_status(self):
        self.client.force_login(self.user_colaborador)

        response = self.client.post(reverse('enviar_documento'), {
            'tipo_documento': self.tipo_doc.id,
            'arquivo': self._arquivo(),
        })

        self.assertEqual(response.status_code, 302)
        self.funcionario.refresh_from_db()
        self.assertEqual(self.funcionario.status, 'aguardando_contrato')

    def test_gerar_contrato_leva_a_aguardando_assinatura(self):
        self.funcionario.status = 'aguardando_contrato'
        self.funcionario.save()
        self.client.force_login(self.user_rh)

        response = self.client.post(reverse('gerar_contrato', args=[self.funcionario.id]))

        self.assertEqual(response.status_code, 200)
        self.funcionario.refresh_from_db()
        self.assertEqual(self.funcionario.status, 'aguardando_assinatura')
        self.assertTrue(
            Documento.objects.filter(funcionario=self.funcionario, tipo_documento__eh_contrato=True).exists()
        )

    def test_reenvio_de_documento_rejeitado_substitui_registro(self):
        documento = Documento.objects.create(
            funcionario=self.funcionario, tipo_documento=self.tipo_doc,
            arquivo=self._arquivo(), status='rejeitado', motivo_rejeicao='Ilegível',
        )
        self.funcionario.status = 'em_validacao'
        self.funcionario.save()
        self.client.force_login(self.user_colaborador)

        response = self.client.post(reverse('enviar_documento'), {
            'tipo_documento': self.tipo_doc.id,
            'arquivo': self._arquivo('novo.pdf'),
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            Documento.objects.filter(funcionario=self.funcionario, tipo_documento=self.tipo_doc).count(), 1
        )
        documento.refresh_from_db()
        self.assertEqual(documento.status, 'pendente')
        self.assertEqual(documento.motivo_rejeicao, '')

    def test_validar_funcionario_bloqueado_com_documento_pendente(self):
        Documento.objects.create(
            funcionario=self.funcionario, tipo_documento=self.tipo_doc, arquivo=self._arquivo(), status='pendente',
        )
        self.funcionario.status = 'em_validacao'
        self.funcionario.save()
        self.client.force_login(self.user_rh)

        self.client.post(reverse('validar_funcionario', args=[self.funcionario.id]))

        self.funcionario.refresh_from_db()
        self.assertEqual(self.funcionario.status, 'em_validacao')

    def test_validar_funcionario_ativa_quando_tudo_aprovado(self):
        Documento.objects.create(
            funcionario=self.funcionario, tipo_documento=self.tipo_doc, arquivo=self._arquivo(), status='aprovado',
        )
        self.funcionario.status = 'em_validacao'
        self.funcionario.save()
        self.client.force_login(self.user_rh)

        self.client.post(reverse('validar_funcionario', args=[self.funcionario.id]))

        self.funcionario.refresh_from_db()
        self.assertEqual(self.funcionario.status, 'ativo')

    def _documento_em_validacao(self):
        self.funcionario.status = 'em_validacao'
        self.funcionario.save()
        return Documento.objects.create(
            funcionario=self.funcionario, tipo_documento=self.tipo_doc, arquivo=self._arquivo(), status='pendente',
        )

    def test_detalhe_abre_documento_no_popup_com_validar_e_rejeitar(self):
        with self.settings(MEDIA_ROOT=tempfile.mkdtemp()):
            documento = self._documento_em_validacao()
            self.client.force_login(self.user_rh)

            response = self.client.get(reverse('detalhe_funcionario', args=[self.funcionario.id]))
            self.assertContains(response, 'id="modal-termo"')
            self.assertContains(response, f'data-aprovar="{reverse("aprovar_documento", args=[documento.id])}"')
            self.assertContains(response, f'data-rejeitar="{reverse("rejeitar_documento", args=[documento.id])}"')

            # O popup carrega o arquivo num iframe: a view precisa permitir isso na mesma origem.
            arquivo = self.client.get(reverse('ver_documento', args=[documento.id]))
            self.assertEqual(arquivo['X-Frame-Options'], 'SAMEORIGIN')

    def test_rejeitar_pelo_popup_volta_ao_detalhe_com_motivo(self):
        with self.settings(MEDIA_ROOT=tempfile.mkdtemp()):
            documento = self._documento_em_validacao()
            self.client.force_login(self.user_rh)
            detalhe = reverse('detalhe_funcionario', args=[self.funcionario.id])

            response = self.client.post(
                reverse('rejeitar_documento', args=[documento.id]), {'motivo': 'Ilegível', 'next': detalhe},
            )

            self.assertRedirects(response, detalhe, fetch_redirect_response=False)
            documento.refresh_from_db()
            self.assertEqual(documento.status, 'rejeitado')
            self.assertEqual(documento.motivo_rejeicao, 'Ilegível')

    def test_documento_enviado_depois_da_ativacao_pode_ser_validado(self):
        with self.settings(MEDIA_ROOT=tempfile.mkdtemp()):
            documento = self._documento_em_validacao()
            self.funcionario.status = 'ativo'
            self.funcionario.save()
            self.client.force_login(self.user_rh)

            response = self.client.get(reverse('detalhe_funcionario', args=[self.funcionario.id]))
            # Só o ícone de olho abre o popup com as ações; não há botão Validar na linha.
            self.assertContains(response, f'data-aprovar="{reverse("aprovar_documento", args=[documento.id])}"', count=1)
            self.assertNotContains(response, 'class="btn-primario btn-sm" data-abrir-termo')

            self.client.post(reverse('aprovar_documento', args=[documento.id]))
            documento.refresh_from_db()
            self.assertEqual(documento.status, 'aprovado')
            self.funcionario.refresh_from_db()
            self.assertEqual(self.funcionario.status, 'ativo')

    def test_documento_ja_analisado_nao_e_revisado_de_novo(self):
        with self.settings(MEDIA_ROOT=tempfile.mkdtemp()):
            documento = self._documento_em_validacao()
            documento.status = 'aprovado'
            documento.save()
            self.client.force_login(self.user_rh)

            self.client.post(reverse('rejeitar_documento', args=[documento.id]), {'motivo': 'x'})
            documento.refresh_from_db()
            self.assertEqual(documento.status, 'aprovado')

            response = self.client.get(reverse('detalhe_funcionario', args=[self.funcionario.id]))
            self.assertNotContains(response, reverse('aprovar_documento', args=[documento.id]))

    def test_aprovar_documento_exige_post(self):
        with self.settings(MEDIA_ROOT=tempfile.mkdtemp()):
            documento = self._documento_em_validacao()
            self.client.force_login(self.user_rh)

            self.client.get(reverse('aprovar_documento', args=[documento.id]))
            documento.refresh_from_db()
            self.assertEqual(documento.status, 'pendente')

            self.client.post(reverse('aprovar_documento', args=[documento.id]))
            documento.refresh_from_db()
            self.assertEqual(documento.status, 'aprovado')


class ResetarSenhaFuncionarioViewTests(TestCase):
    """RH/gestor gera uma nova senha aleatória pra um funcionário que já tem acesso."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='Produção')
        self.funcionario = Funcionario.objects.create(
            nome='Colaborador Teste', cpf='12345678901', cargo='Analista', setor=self.setor, status='ativo',
        )
        self.user_colaborador = User.objects.create_user(username='12345678901', password='senha123')
        self.usuario_colaborador = Usuario.objects.create(
            user=self.user_colaborador, funcionario=self.funcionario, papel='colaborador',
        )

        funcionario_rh = Funcionario.objects.create(
            nome='RH Teste', cpf='98765432100', cargo='RH', setor=self.setor, status='ativo',
        )
        self.user_rh = User.objects.create_user(username='98765432100', password='senha123')
        Usuario.objects.create(user=self.user_rh, funcionario=funcionario_rh, papel='rh')

    def test_gera_nova_senha_e_marca_como_provisoria(self):
        senha_antiga = self.user_colaborador.password
        self.client.force_login(self.user_rh)

        response = self.client.post(reverse('resetar_senha_funcionario', args=[self.funcionario.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Senha redefinida')
        self.user_colaborador.refresh_from_db()
        self.usuario_colaborador.refresh_from_db()
        self.assertNotEqual(self.user_colaborador.password, senha_antiga)
        self.assertTrue(self.usuario_colaborador.senha_provisoria)

    def test_nova_senha_exibida_realmente_funciona(self):
        self.client.force_login(self.user_rh)

        response = self.client.post(reverse('resetar_senha_funcionario', args=[self.funcionario.id]))

        senha_gerada = response.context['senha_gerada']
        self.user_colaborador.refresh_from_db()
        self.assertTrue(self.user_colaborador.check_password(senha_gerada))

    def test_bloqueia_se_funcionario_ainda_nao_tem_acesso(self):
        sem_conta = Funcionario.objects.create(
            nome='Sem Conta', cpf='11111111111', cargo='Analista', setor=self.setor, status='ativo',
        )
        self.client.force_login(self.user_rh)

        response = self.client.post(reverse('resetar_senha_funcionario', args=[sem_conta.id]))

        self.assertRedirects(response, reverse('detalhe_funcionario', args=[sem_conta.id]))

    def test_colaborador_comum_nao_acessa(self):
        self.client.force_login(self.user_colaborador)

        response = self.client.post(reverse('resetar_senha_funcionario', args=[self.funcionario.id]))

        self.assertRedirects(response, reverse('dashboard'))

    def test_gestor_de_outro_setor_nao_acessa(self):
        outro_setor = Setor.objects.create(nome='Outro Setor')
        gestor_funcionario = Funcionario.objects.create(
            nome='Gestor Teste', cpf='22222222222', cargo='Gestor', setor=outro_setor, status='ativo',
        )
        user_gestor = User.objects.create_user(username='22222222222', password='senha123')
        Usuario.objects.create(user=user_gestor, funcionario=gestor_funcionario, papel='gestor')
        self.client.force_login(user_gestor)
        senha_antiga = self.user_colaborador.password

        response = self.client.post(reverse('resetar_senha_funcionario', args=[self.funcionario.id]))

        self.assertRedirects(response, reverse('listar_funcionarios'))
        self.user_colaborador.refresh_from_db()
        self.assertEqual(self.user_colaborador.password, senha_antiga)


class GerarAcessoFuncionarioViewTests(TestCase):
    """Primeiro acesso de quem já está cadastrado: RH/DP ou o gestor do setor geram, com senha aleatória."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='Produção')
        self.funcionario = Funcionario.objects.create(
            nome='Sem Acesso', cpf='11111111111', cargo='Operador', setor=self.setor, status='ativo',
        )
        gestor = Funcionario.objects.create(
            nome='Gestor Teste', cpf='22222222222', cargo='Gestor', setor=self.setor, status='ativo',
        )
        self.user_gestor = User.objects.create_user(username='22222222222', password='senha123')
        Usuario.objects.create(user=self.user_gestor, funcionario=gestor, papel='gestor')

    def test_gestor_do_setor_gera_acesso_com_senha_aleatoria(self):
        self.client.force_login(self.user_gestor)

        response = self.client.post(reverse('gerar_acesso_funcionario', args=[self.funcionario.id]))

        senha_gerada = response.context['senha_gerada']
        user = User.objects.get(username='11111111111')
        self.assertTrue(user.check_password(senha_gerada))
        self.assertTrue(user.usuario.senha_provisoria)

    def test_cada_acesso_recebe_uma_senha_diferente(self):
        outro = Funcionario.objects.create(
            nome='Outro Sem Acesso', cpf='33333333333', cargo='Operador', setor=self.setor, status='ativo',
        )
        self.client.force_login(self.user_gestor)

        senha_1 = self.client.post(reverse('gerar_acesso_funcionario', args=[self.funcionario.id])).context['senha_gerada']
        senha_2 = self.client.post(reverse('gerar_acesso_funcionario', args=[outro.id])).context['senha_gerada']

        self.assertNotEqual(senha_1, senha_2)

    def test_gestor_de_outro_setor_nao_gera(self):
        outro_setor = Setor.objects.create(nome='Outro Setor')
        de_fora = Funcionario.objects.create(
            nome='De Fora', cpf='44444444444', cargo='Operador', setor=outro_setor, status='ativo',
        )
        self.client.force_login(self.user_gestor)

        response = self.client.post(reverse('gerar_acesso_funcionario', args=[de_fora.id]))

        self.assertRedirects(response, reverse('listar_funcionarios'))
        self.assertFalse(User.objects.filter(username='44444444444').exists())


class LoginGestorPorNomeTests(TestCase):
    """Gestor entra pelo primeiro nome (ou CPF); o resto só pelo CPF."""

    def setUp(self):
        cache.clear()
        self.setor = Setor.objects.create(nome='Produção')

    def _usuario(self, nome, cpf, papel):
        funcionario = Funcionario.objects.create(nome=nome, cpf=cpf, cargo='X', setor=self.setor, status='ativo')
        user = User.objects.create_user(username=cpf, password='Senha!123')
        return Usuario.objects.create(user=user, funcionario=funcionario, papel=papel)

    def _entrar(self, login):
        return self.client.post(reverse('login'), {'username': login, 'password': 'Senha!123'})

    def test_gestor_recebe_primeiro_nome_sem_acento(self):
        gestor = self._usuario('ANÁLIA ANDRESSA RODRIGUES', '11111111111', 'gestor')

        self.assertEqual(gestor.login_gestor, 'analia')

    def test_nome_repetido_ganha_numero(self):
        primeiro = self._usuario('DANIEL SOUSA', '11111111111', 'gestor')
        segundo = self._usuario('Daniel Pereira', '22222222222', 'gestor')

        self.assertEqual(primeiro.login_gestor, 'daniel')
        self.assertEqual(segundo.login_gestor, 'daniel2')

    def test_gestor_entra_pelo_nome_sem_diferenciar_maiusculas(self):
        self._usuario('Romario Ribeiro', '11111111111', 'gestor')

        self.assertRedirects(self._entrar(' Romario '), reverse('dashboard'), fetch_redirect_response=False)

    def test_gestor_continua_entrando_pelo_cpf(self):
        self._usuario('Romario Ribeiro', '11111111111', 'gestor')

        self.assertRedirects(self._entrar('11111111111'), reverse('dashboard'), fetch_redirect_response=False)

    def test_colaborador_nao_tem_login_por_nome(self):
        colaborador = self._usuario('Maria Silva', '11111111111', 'colaborador')

        self.assertIsNone(colaborador.login_gestor)
        self.assertContains(self._entrar('maria'), 'inválidos')

    def test_deixar_de_ser_gestor_inativa_o_login_por_nome(self):
        gestor = self._usuario('Romario Ribeiro', '11111111111', 'gestor')

        gestor.papel = 'colaborador'
        gestor.save()

        gestor.refresh_from_db()
        self.assertIsNone(gestor.login_gestor)
        self.assertContains(self._entrar('romario'), 'inválidos')
        self.assertRedirects(self._entrar('11111111111'), reverse('dashboard'), fetch_redirect_response=False)


class TrocarSenhasPadraoCommandTests(TestCase):
    def _usuario(self, cpf, senha, provisoria=True):
        setor, _ = Setor.objects.get_or_create(nome='Produção')
        funcionario = Funcionario.objects.create(nome=f'Func {cpf}', cpf=cpf, cargo='Operador', setor=setor, status='ativo')
        user = User.objects.create_user(username=cpf, password=senha)
        Usuario.objects.create(user=user, funcionario=funcionario, papel='colaborador', senha_provisoria=provisoria)
        return user

    def test_troca_so_quem_ainda_esta_com_a_senha_antiga(self):
        com_padrao = self._usuario('11111111111', 'Padrao@123')
        ja_trocou = self._usuario('22222222222', 'MinhaSenha!9', provisoria=False)

        call_command('trocar_senhas_padrao', 'Padrao@123', stdout=io.StringIO())

        com_padrao.refresh_from_db()
        ja_trocou.refresh_from_db()
        self.assertFalse(com_padrao.check_password('Padrao@123'))
        self.assertTrue(ja_trocou.check_password('MinhaSenha!9'))

    def test_dry_run_nao_grava(self):
        com_padrao = self._usuario('11111111111', 'Padrao@123')

        call_command('trocar_senhas_padrao', 'Padrao@123', '--dry-run', stdout=io.StringIO())

        com_padrao.refresh_from_db()
        self.assertTrue(com_padrao.check_password('Padrao@123'))


class RecuperarAcessoTests(TestCase):
    """"Recuperar acesso" na tela de login: o colaborador pede, o RH/DP gera a nova senha."""

    def setUp(self):
        cache.clear()
        self.setor = Setor.objects.create(nome='Produção')
        self.funcionario = Funcionario.objects.create(
            nome='Colaborador Teste', cpf='12345678901', cargo='Analista', setor=self.setor, status='ativo',
        )
        self.user_colaborador = User.objects.create_user(username='12345678901', password='senha123')
        self.usuario_colaborador = Usuario.objects.create(
            user=self.user_colaborador, funcionario=self.funcionario, papel='colaborador',
        )

        funcionario_rh = Funcionario.objects.create(
            nome='RH Teste', cpf='98765432100', cargo='RH', setor=self.setor, status='ativo',
        )
        self.user_rh = User.objects.create_user(username='98765432100', password='senha123')
        Usuario.objects.create(user=self.user_rh, funcionario=funcionario_rh, papel='rh')

    def test_pedido_com_cpf_formatado_cria_solicitacao(self):
        response = self.client.post(reverse('recuperar_acesso'), {'cpf': '123.456.789-01'})

        self.assertContains(response, 'Pedido enviado')
        self.assertEqual(SolicitacaoSenha.pendentes().filter(usuario=self.usuario_colaborador).count(), 1)

    def test_cpf_desconhecido_responde_igual_e_nao_cria_nada(self):
        response = self.client.post(reverse('recuperar_acesso'), {'cpf': '000.000.000-00'})

        self.assertContains(response, 'Pedido enviado')
        self.assertFalse(SolicitacaoSenha.objects.exists())

    def test_pedido_repetido_nao_duplica(self):
        self.client.post(reverse('recuperar_acesso'), {'cpf': '12345678901'})
        self.client.post(reverse('recuperar_acesso'), {'cpf': '12345678901'})

        self.assertEqual(SolicitacaoSenha.pendentes().count(), 1)

    def test_limite_de_pedidos_por_ip(self):
        for _ in range(views.LIMITE_PEDIDOS_SENHA_IP):
            self.client.post(reverse('recuperar_acesso'), {'cpf': '000'})

        response = self.client.post(reverse('recuperar_acesso'), {'cpf': '12345678901'})

        self.assertContains(response, 'Muitos pedidos seguidos')
        self.assertFalse(SolicitacaoSenha.objects.exists())

    def test_rh_ve_pedido_e_badge(self):
        SolicitacaoSenha.objects.create(usuario=self.usuario_colaborador)
        self.client.force_login(self.user_rh)

        response = self.client.get(reverse('pedidos_senha'))

        self.assertContains(response, 'Colaborador Teste')
        self.assertEqual(response.context['badge_pedidos_senha'], 1)

    def test_colaborador_nao_ve_pedidos(self):
        self.client.force_login(self.user_colaborador)

        response = self.client.get(reverse('pedidos_senha'))

        self.assertRedirects(response, reverse('dashboard'))

    def test_gerar_nova_senha_fecha_o_pedido(self):
        pedido = SolicitacaoSenha.objects.create(usuario=self.usuario_colaborador)
        self.client.force_login(self.user_rh)

        self.client.post(reverse('resetar_senha_funcionario', args=[self.funcionario.id]))

        pedido.refresh_from_db()
        self.assertIsNotNone(pedido.atendida_em)
        self.assertEqual(pedido.atendida_por, self.user_rh)
        self.assertFalse(SolicitacaoSenha.pendentes().exists())


class AssinaturaGovViewTests(TestCase):
    """Tela "Assinar com Gov.br" -- link externo + download/reenvio de tudo que espera assinatura, sem API."""

    def setUp(self):
        self.override = self.settings(MEDIA_ROOT=tempfile.mkdtemp())
        self.override.enable()
        self.addCleanup(self.override.disable)

        setor = Setor.objects.create(nome='Produção')
        self.tipo_contrato = TipoDocumento.objects.create(
            nome='Contrato de Trabalho', obrigatorio=True, eh_contrato=True,
        )
        self.funcionario = Funcionario.objects.create(
            nome='Colaborador Teste', cpf='12345678902', cargo='Analista', setor=setor,
            status='aguardando_assinatura',
        )
        self.user = User.objects.create_user(username='12345678902', password='senha123')
        Usuario.objects.create(user=self.user, funcionario=self.funcionario, papel='colaborador')
        self.client.force_login(self.user)

    def _arquivo(self, nome='arquivo.pdf'):
        return SimpleUploadedFile(nome, b'conteudo', content_type='application/pdf')

    def test_mostra_link_externo_e_estado_vazio_sem_nada_para_assinar(self):
        response = self.client.get(reverse('minhas_assinaturas'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'https://assinador.iti.br/')
        self.assertContains(response, 'Nenhum documento esperando a sua assinatura')

    def test_mostra_card_de_download_e_reenvio_quando_ha_contrato(self):
        Documento.objects.create(
            funcionario=self.funcionario, tipo_documento=self.tipo_contrato,
            arquivo=self._arquivo(), status='pendente',
        )

        response = self.client.get(reverse('minhas_assinaturas'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Contrato de Trabalho')
        self.assertNotContains(response, 'Nenhum documento esperando a sua assinatura')

    def test_mostra_so_comodatos_e_fichas_de_epi_esperando_assinatura(self):
        comodato = TermoComodato.objects.create(
            funcionario=self.funcionario, modalidade='uniforme', data_emprestimo=date(2026, 10, 1),
            status='aguardando_assinatura', motivo_rejeicao='Assinatura ilegível',
        )
        comodato.arquivo_gerado.save('termo.pdf', ContentFile(b'%PDF-1.4'))
        ficha = FichaEPI.objects.create(funcionario=self.funcionario, data_entrega=date(2026, 10, 2))
        ficha.arquivo_gerado.save('ficha.pdf', ContentFile(b'%PDF-1.4'))
        # Ainda sem PDF gerado e já enviado: nenhum dos dois tem o que assinar.
        sem_pdf = TermoComodato.objects.create(
            funcionario=self.funcionario, modalidade='equipamento', data_emprestimo=date(2026, 10, 3),
            status='aguardando_assinatura',
        )
        enviado = FichaEPI.objects.create(
            funcionario=self.funcionario, data_entrega=date(2026, 10, 4), status='em_validacao',
        )
        enviado.arquivo_gerado.save('ficha2.pdf', ContentFile(b'%PDF-1.4'))

        response = self.client.get(reverse('minhas_assinaturas'))

        self.assertEqual(list(response.context['comodatos']), [comodato])
        self.assertEqual(list(response.context['fichas_epi']), [ficha])
        self.assertContains(response, reverse('ver_comodato', args=[comodato.id, 'gerado']))
        self.assertContains(response, reverse('ver_ficha_epi', args=[ficha.id, 'gerado']))
        self.assertContains(response, 'Assinatura ilegível')
        self.assertNotContains(response, reverse('reenviar_comodato_assinado', args=[sem_pdf.id]))
        self.assertNotContains(response, reverse('reenviar_epi_assinado', args=[enviado.id]))
        self.assertNotContains(response, 'Nenhum documento esperando a sua assinatura')

    def test_envio_do_comodato_assinado_pela_tela_volta_para_ela(self):
        comodato = TermoComodato.objects.create(
            funcionario=self.funcionario, modalidade='uniforme', data_emprestimo=date(2026, 10, 1),
            status='aguardando_assinatura',
        )
        comodato.arquivo_gerado.save('termo.pdf', ContentFile(b'%PDF-1.4'))

        response = self.client.post(reverse('reenviar_comodato_assinado', args=[comodato.id]), {
            'arquivo_assinado': self._arquivo('assinado.pdf'), 'origem': 'assinatura_gov',
        })

        self.assertRedirects(response, reverse('minhas_assinaturas'))
        comodato.refresh_from_db()
        self.assertEqual(comodato.status, 'em_validacao')

    def test_envio_da_ficha_de_epi_assinada_pela_tela_volta_para_ela(self):
        ficha = FichaEPI.objects.create(funcionario=self.funcionario, data_entrega=date(2026, 10, 1))
        ficha.arquivo_gerado.save('ficha.pdf', ContentFile(b'%PDF-1.4'))

        response = self.client.post(reverse('reenviar_epi_assinado', args=[ficha.id]), {
            'arquivo_assinado': self._arquivo('assinada.pdf'), 'origem': 'assinatura_gov',
        })

        self.assertRedirects(response, reverse('minhas_assinaturas'))
        ficha.refresh_from_db()
        self.assertEqual(ficha.status, 'em_validacao')

    def test_envio_sem_origem_continua_voltando_para_meus_documentos(self):
        ficha = FichaEPI.objects.create(funcionario=self.funcionario, data_entrega=date(2026, 10, 1))
        ficha.arquivo_gerado.save('ficha.pdf', ContentFile(b'%PDF-1.4'))

        response = self.client.post(reverse('reenviar_epi_assinado', args=[ficha.id]), {
            'arquivo_assinado': self._arquivo('assinada.pdf'),
        })

        self.assertRedirects(response, reverse('meus_documentos'))

    def test_reenvio_com_origem_assinatura_gov_redireciona_de_volta_e_avanca_status(self):
        Documento.objects.create(
            funcionario=self.funcionario, tipo_documento=self.tipo_contrato,
            arquivo=self._arquivo(), status='pendente',
        )

        response = self.client.post(reverse('enviar_documento'), {
            'tipo_documento': self.tipo_contrato.id,
            'arquivo': self._arquivo('assinado.pdf'),
            'origem': 'assinatura_gov',
        })

        self.assertRedirects(response, reverse('minhas_assinaturas'))
        self.funcionario.refresh_from_db()
        self.assertEqual(self.funcionario.status, 'em_validacao')

    def test_sem_perfil_nao_quebra(self):
        self.client.force_login(User.objects.create_user(username='semperfil_assinatura', password='senha123'))

        response = self.client.get(reverse('minhas_assinaturas'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'funcionarios/sem_perfil.html')


class EditarDadosFuncionarioViewTests(TestCase):
    """RH edita matrícula, empresa, setor, cargo, salário e admissão na ficha do funcionário."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='Produção')
        self.outro_setor = Setor.objects.create(nome='Moinho')
        self.funcionario = Funcionario.objects.create(
            nome='Colaborador Teste', cpf='12345678901', matricula='100', cargo='AUXILIAR DE PRODUCAO',
            setor=self.setor, status='ativo',
        )
        self.colega = Funcionario.objects.create(
            nome='Colega Teste', cpf='12345678902', matricula='200', cargo='AUXILIAR DE PRODUCAO',
            setor=self.setor, status='ativo',
        )
        funcionario_rh = Funcionario.objects.create(
            nome='RH Teste', cpf='98765432100', cargo='RH', setor=self.setor, status='ativo',
        )
        self.user_rh = User.objects.create_user(username='98765432100', password='senha123')
        Usuario.objects.create(user=self.user_rh, funcionario=funcionario_rh, papel='rh')
        self.url = reverse('editar_dados_funcionario', args=[self.funcionario.id])
        self.client.force_login(self.user_rh)

    def _dados(self, **kwargs):
        dados = {
            'matricula': '100', 'codigo_empresa': '453', 'setor': self.setor.id,
            'cargo': 'AUXILIAR DE PRODUCAO', 'salario': '', 'data_admissao': '',
        }
        dados.update(kwargs)
        return dados

    def test_salva_todos_os_campos(self):
        response = self.client.post(self.url, self._dados(
            matricula='101', codigo_empresa='460', setor=self.outro_setor.id, cargo='Moleiro',
            salario='2500.50', data_admissao='2024-03-01',
        ))

        self.assertRedirects(response, reverse('detalhe_funcionario', args=[self.funcionario.id]))
        self.funcionario.refresh_from_db()
        self.assertEqual(self.funcionario.matricula, '101')
        self.assertEqual(self.funcionario.codigo_empresa, 460)
        self.assertEqual(self.funcionario.setor, self.outro_setor)
        self.assertEqual(self.funcionario.cargo, 'Moleiro')
        self.assertEqual(self.funcionario.salario, Decimal('2500.50'))
        self.assertEqual(self.funcionario.data_admissao, date(2024, 3, 1))

    def test_correcao_de_grafia_do_proprio_cargo_e_salva(self):
        # Antes, a busca por cargo igual (sem caixa/acento) achava o próprio cadastro e desfazia a correção.
        self.client.post(self.url, self._dados(cargo='Auxiliar de Produção'))

        self.funcionario.refresh_from_db()
        self.colega.refresh_from_db()
        self.assertEqual(self.funcionario.cargo, 'Auxiliar de Produção')
        self.assertEqual(self.colega.cargo, 'AUXILIAR DE PRODUCAO')

    def test_cargo_de_outra_pessoa_reaproveita_a_grafia_cadastrada(self):
        Funcionario.objects.create(nome='Operador', cpf='12345678903', cargo='Operador de Máquina', setor=self.setor)

        self.client.post(self.url, self._dados(cargo='operador de  máquina'))

        self.funcionario.refresh_from_db()
        self.assertEqual(self.funcionario.cargo, 'Operador de Máquina')

    def test_erro_volta_para_a_ficha_com_o_erro_e_o_que_foi_digitado(self):
        response = self.client.post(self.url, self._dados(matricula='200', cargo='Moleiro'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['dados_form'].errors['matricula'])
        self.assertContains(response, 'value="Moleiro"')
        self.assertContains(response, 'data-avisar-nao-salvo="pendente"')
        self.funcionario.refresh_from_db()
        self.assertEqual(self.funcionario.matricula, '100')
        self.assertEqual(self.funcionario.cargo, 'AUXILIAR DE PRODUCAO')

    def test_ficha_mostra_botao_salvar(self):
        response = self.client.get(reverse('detalhe_funcionario', args=[self.funcionario.id]))

        self.assertContains(response, 'Salvar alterações')
        self.assertContains(response, 'data-avisar-nao-salvo=""')


class AtestadoFormTests(TestCase):
    def test_data_fim_antes_da_data_inicio_e_rejeitada(self):
        form = AtestadoForm(data={'data_inicio': '2026-08-10', 'data_fim': '2026-08-05', 'motivo': ''})

        self.assertFalse(form.is_valid())

    def test_periodo_valido_e_aceito(self):
        form = AtestadoForm(data={'data_inicio': '2026-08-05', 'data_fim': '2026-08-10', 'motivo': 'Gripe'})

        self.assertTrue(form.is_valid())


class LancarAtestadoViewTests(TestCase):
    """RH/gestor registra um atestado (período de afastamento) pra um funcionário."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='Produção')
        self.funcionario = Funcionario.objects.create(
            nome='Colaborador Teste', cpf='12345678901', cargo='Analista', setor=self.setor, status='ativo',
        )
        funcionario_rh = Funcionario.objects.create(
            nome='RH Teste', cpf='98765432100', cargo='RH', setor=self.setor, status='ativo',
        )
        self.user_rh = User.objects.create_user(username='98765432100', password='senha123')
        Usuario.objects.create(user=self.user_rh, funcionario=funcionario_rh, papel='rh')

        user_colaborador = User.objects.create_user(username='12345678901', password='senha123')
        Usuario.objects.create(user=user_colaborador, funcionario=self.funcionario, papel='colaborador')
        self.user_colaborador = user_colaborador

    def test_rh_lanca_atestado(self):
        self.client.force_login(self.user_rh)

        response = self.client.post(reverse('lancar_atestado', args=[self.funcionario.id]), {
            'data_inicio': '2026-08-05', 'data_fim': '2026-08-07', 'motivo': 'Gripe',
        })

        self.assertRedirects(response, reverse('detalhe_funcionario', args=[self.funcionario.id]))
        atestado = Atestado.objects.get(funcionario=self.funcionario)
        self.assertEqual(atestado.data_inicio, date(2026, 8, 5))
        self.assertEqual(atestado.data_fim, date(2026, 8, 7))

    def test_gestor_ve_salario_e_lanca_atestado_na_ficha_da_equipe(self):
        self.funcionario.salario = Decimal('2500.00')
        self.funcionario.save()
        gestor = Funcionario.objects.create(nome='Gestor Teste', cpf='55544433322', cargo='Gerente', setor=self.setor, status='ativo')
        user_gestor = User.objects.create_user(username='55544433322', password='senha123')
        Usuario.objects.create(user=user_gestor, funcionario=gestor, papel='gestor')
        self.client.force_login(user_gestor)

        ficha = self.client.get(reverse('detalhe_funcionario', args=[self.funcionario.id]))
        self.client.post(reverse('lancar_atestado', args=[self.funcionario.id]), {
            'data_inicio': '2026-08-05', 'data_fim': '2026-08-07', 'motivo': 'Gripe',
        })

        self.assertContains(ficha, 'R$ 2.500,00')
        self.assertContains(ficha, 'Lançar atestado')
        self.assertEqual(Atestado.objects.get(funcionario=self.funcionario).lancado_por, user_gestor)

    def test_colaborador_comum_nao_lanca_atestado_de_terceiros(self):
        self.client.force_login(self.user_colaborador)

        self.client.post(reverse('lancar_atestado', args=[self.funcionario.id]), {
            'data_inicio': '2026-08-05', 'data_fim': '2026-08-07', 'motivo': 'Gripe',
        })

        self.assertFalse(Atestado.objects.filter(funcionario=self.funcionario).exists())

class ServiceWorkerTests(TestCase):
    """
    O service worker só pode guardar arquivo estático.

    Página HTML sai do servidor amarrada a quem pediu -- token CSRF do cookie
    daquele momento e dados da sessão. Guardar e reservir uma cópia derrubava o
    login com 403 ("CSRF token from POST incorrect") e, no aparelho
    compartilhado, mostraria a tela de um usuário para o próximo.
    """

    def test_service_worker_e_servido_com_escopo_na_raiz(self):
        resposta = self.client.get(reverse('service_worker'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta['Service-Worker-Allowed'], '/')

    def test_service_worker_nao_guarda_html_em_cache(self):
        script = self.client.get(reverse('service_worker')).content.decode('utf-8')

        # O único `cache.put` do script tem de estar dentro do ramo de
        # /static/ e /media/, e não no caminho de navegação.
        trecho_navegacao = script.split("url.pathname.startsWith('/static/')")[-1]
        trecho_navegacao = trecho_navegacao.split('return;', 1)[-1]

        self.assertNotIn('.put(', trecho_navegacao)
        self.assertIn('PAGINA_OFFLINE', trecho_navegacao)


class CriarComodatoViewTests(TestCase):
    """RH cria um comodato (cabeçalho + itens) numa única submissão."""

    def setUp(self):
        setor = Setor.objects.create(nome='Produção')
        self.funcionario = Funcionario.objects.create(
            nome='Colaborador Teste', cpf='12345678901', cargo='Analista', setor=setor, status='ativo',
        )
        funcionario_rh = Funcionario.objects.create(
            nome='RH Teste', cpf='98765432100', cargo='RH', setor=setor, status='ativo',
        )
        self.user_rh = User.objects.create_user(username='98765432100', password='senha123')
        Usuario.objects.create(user=self.user_rh, funcionario=funcionario_rh, papel='rh')
        self.client.force_login(self.user_rh)

        self.camisa = ItemCatalogoComodato.objects.get(nome='Camisa Uniforme')
        self.celular = ItemCatalogoComodato.objects.get(nome='Celular')

    def _post(self, modalidade, linhas):
        dados = {
            'funcionario': self.funcionario.id, 'modalidade': modalidade, 'data_emprestimo': '2026-10-01',
            'itens-TOTAL_FORMS': '5', 'itens-INITIAL_FORMS': '0',
            'itens-MIN_NUM_FORMS': '0', 'itens-MAX_NUM_FORMS': '1000',
        }
        for i in range(5):
            dados[f'itens-{i}-quantidade'] = '1'
        for i, linha in enumerate(linhas):
            for campo, valor in linha.items():
                dados[f'itens-{i}-{campo}'] = valor
        return self.client.post(reverse('criar_comodato'), dados)

    def test_cria_comodato_de_uniforme_com_valor(self):
        response = self._post('uniforme', [
            {'item_catalogo': self.camisa.id, 'descricao': 'Azul', 'tamanho': 'G', 'quantidade': '2', 'valor_unitario': '50'},
        ])

        self.assertRedirects(response, reverse('listar_comodatos'))
        termo = TermoComodato.objects.get(funcionario=self.funcionario)
        self.assertEqual(termo.gerado_por, self.user_rh)
        item = termo.itens.get()
        self.assertEqual((item.item_catalogo, item.tamanho, item.quantidade), (self.camisa, 'G', 2))
        self.assertEqual(str(item.valor_unitario), '50.00')

    def test_equipamento_guarda_numero_de_serie_e_descarta_tamanho(self):
        self._post('equipamento', [
            {'item_catalogo': self.celular.id, 'numero_serie': '356938035643809', 'tamanho': 'G', 'valor_unitario': '1200.00'},
        ])

        item = ItemComodato.objects.get()
        self.assertEqual(item.numero_serie, '356938035643809')
        self.assertEqual(item.tamanho, '')
        self.assertEqual(str(item.valor_unitario), '1200.00')

    def test_uniforme_descarta_numero_de_serie(self):
        self._post('uniforme', [{'item_catalogo': self.camisa.id, 'numero_serie': 'ABC123'}])

        self.assertEqual(ItemComodato.objects.get().numero_serie, '')

    def test_numero_de_serie_exige_quantidade_um(self):
        response = self._post('equipamento', [
            {'item_catalogo': self.celular.id, 'numero_serie': 'ABC123', 'quantidade': '2'},
        ])

        self.assertFalse(TermoComodato.objects.exists())
        self.assertIn('quantidade', response.context['formset'].forms[0].errors)

    def test_linha_preenchida_sem_item_do_catalogo_e_recusada(self):
        response = self._post('uniforme', [{'descricao': 'Camisa azul'}])

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TermoComodato.objects.exists())
        self.assertIn('item_catalogo', response.context['formset'].forms[0].errors)

    def test_item_de_outra_modalidade_e_recusado(self):
        self._post('uniforme', [{'item_catalogo': self.celular.id}])

        self.assertFalse(TermoComodato.objects.exists())

    def test_comodato_sem_itens_e_recusado(self):
        response = self._post('uniforme', [])

        self.assertEqual(response.status_code, 200)
        self.assertFalse(TermoComodato.objects.exists())
        self.assertTrue(response.context['formset'].non_form_errors())

    def test_linha_marcada_para_exclusao_nao_e_salva(self):
        self._post('uniforme', [
            {'item_catalogo': self.camisa.id},
            {'descricao': 'Linha descartada', 'DELETE': 'on'},
        ])

        self.assertEqual(ItemComodato.objects.count(), 1)

    def test_listagem_abre(self):
        response = self.client.get(reverse('listar_comodatos'))

        self.assertEqual(response.status_code, 200)

    def test_tela_de_criacao_traz_dados_da_busca_e_do_catalogo(self):
        response = self.client.get(reverse('criar_comodato'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="dados-funcionarios"')
        self.assertContains(response, 'Colaborador Teste')
        self.assertContains(response, f'"{self.celular.id}": "equipamento"')

    def test_contrato_de_uniforme_lista_modelo_tamanho_quantidade_e_valor(self):
        from django.template.loader import render_to_string

        termo = TermoComodato.objects.create(
            funcionario=self.funcionario, modalidade='uniforme', data_emprestimo=date(2026, 10, 1),
        )
        ItemComodato.objects.create(
            termo=termo, item_catalogo=self.camisa, descricao='Azul', tamanho='G',
            quantidade=2, valor_unitario='1200.50',
        )

        html = render_to_string('funcionarios/contratos/comodato_uniforme.html', {'itens': termo.itens.all()})

        self.assertNotIn('Código', html)
        self.assertLess(html.index('Modelo'), html.index('Tamanho'))
        self.assertLess(html.index('Tamanho'), html.index('Quantidade'))
        self.assertLess(html.index('Quantidade'), html.index('Valor unitário'))
        self.assertIn('R$ 1.200,50', html)


class ValidarComodatoViewTests(TestCase):
    """RH aprova ou rejeita o termo de comodato devolvido assinado pelo colaborador."""

    def setUp(self):
        setor = Setor.objects.create(nome='Produção')
        self.funcionario = Funcionario.objects.create(
            nome='Colaborador Teste', cpf='12345678901', cargo='Analista', setor=setor, status='ativo',
        )
        funcionario_rh = Funcionario.objects.create(
            nome='RH Teste', cpf='98765432100', cargo='RH', setor=setor, status='ativo',
        )
        self.user_rh = User.objects.create_user(username='98765432100', password='senha123')
        Usuario.objects.create(user=self.user_rh, funcionario=funcionario_rh, papel='rh')
        self.user_colaborador = User.objects.create_user(username='12345678901', password='senha123')
        Usuario.objects.create(user=self.user_colaborador, funcionario=self.funcionario, papel='colaborador')

        self.comodato = TermoComodato.objects.create(
            funcionario=self.funcionario, modalidade='uniforme',
            data_emprestimo=date(2026, 10, 1), status='em_validacao',
        )
        self.url_detalhe = reverse('detalhe_funcionario', args=[self.funcionario.id])

    def test_rh_aprova_comodato(self):
        self.client.force_login(self.user_rh)

        response = self.client.post(reverse('aprovar_comodato', args=[self.comodato.id]))

        self.assertRedirects(response, self.url_detalhe, fetch_redirect_response=False)
        self.comodato.refresh_from_db()
        self.assertEqual(self.comodato.status, 'ativo')
        self.assertEqual(self.comodato.validado_por, self.user_rh)
        self.assertIsNotNone(self.comodato.validado_em)

    def test_validando_pela_lista_volta_para_a_fila(self):
        self.client.force_login(self.user_rh)
        fila = reverse('listar_comodatos') + '?pendentes=1'

        response = self.client.post(reverse('aprovar_comodato', args=[self.comodato.id]), {'next': fila})

        self.assertRedirects(response, fila, fetch_redirect_response=False)

    def test_next_para_outro_site_e_ignorado(self):
        self.client.force_login(self.user_rh)

        response = self.client.post(
            reverse('rejeitar_comodato', args=[self.comodato.id]),
            {'motivo': 'Ilegível', 'next': 'https://site-malicioso.com/'},
        )

        self.assertRedirects(response, self.url_detalhe, fetch_redirect_response=False)

    def test_lista_poe_pendentes_primeiro_e_badge_vira_atalho(self):
        TermoComodato.objects.create(
            funcionario=self.funcionario, modalidade='equipamento',
            data_emprestimo=date(2026, 10, 2), status='ativo',
        )
        self.client.force_login(self.user_rh)

        response = self.client.get(reverse('listar_comodatos') + '?pendentes=1')

        self.assertEqual(response.context['comodatos'][0], self.comodato)
        self.assertContains(response, 'data-abrir-pendente="1"')
        self.assertContains(response, 'href="%s?pendentes=1"' % reverse('listar_comodatos'))

    def test_rh_rejeita_comodato_com_motivo_e_volta_para_assinatura(self):
        self.client.force_login(self.user_rh)

        self.client.post(reverse('rejeitar_comodato', args=[self.comodato.id]), {'motivo': 'Assinatura ilegível'})

        self.comodato.refresh_from_db()
        self.assertEqual(self.comodato.status, 'aguardando_assinatura')
        self.assertEqual(self.comodato.motivo_rejeicao, 'Assinatura ilegível')

    def test_rejeicao_sem_motivo_nao_altera_status(self):
        self.client.force_login(self.user_rh)

        self.client.post(reverse('rejeitar_comodato', args=[self.comodato.id]), {'motivo': ''})

        self.comodato.refresh_from_db()
        self.assertEqual(self.comodato.status, 'em_validacao')

    def test_so_valida_comodato_em_validacao(self):
        self.comodato.status = 'aguardando_assinatura'
        self.comodato.save()
        self.client.force_login(self.user_rh)

        self.client.post(reverse('aprovar_comodato', args=[self.comodato.id]))

        self.comodato.refresh_from_db()
        self.assertEqual(self.comodato.status, 'aguardando_assinatura')

    def test_colaborador_nao_aprova_o_proprio_comodato(self):
        self.client.force_login(self.user_colaborador)

        self.client.post(reverse('aprovar_comodato', args=[self.comodato.id]))

        self.comodato.refresh_from_db()
        self.assertEqual(self.comodato.status, 'em_validacao')

    def test_reenvio_limpa_motivo_e_volta_para_validacao(self):
        self.comodato.status = 'aguardando_assinatura'
        self.comodato.motivo_rejeicao = 'Assinatura ilegível'
        self.comodato.save()
        self.client.force_login(self.user_colaborador)

        with self.settings(MEDIA_ROOT=tempfile.mkdtemp()):
            self.client.post(reverse('reenviar_comodato_assinado', args=[self.comodato.id]), {
                'arquivo_assinado': SimpleUploadedFile('termo.pdf', b'%PDF-1.4', content_type='application/pdf'),
            })

        self.comodato.refresh_from_db()
        self.assertEqual(self.comodato.status, 'em_validacao')
        self.assertEqual(self.comodato.motivo_rejeicao, '')

    def test_colaborador_nao_reenvia_termo_ja_validado(self):
        self.comodato.status = 'ativo'
        self.comodato.save()
        self.client.force_login(self.user_colaborador)

        self.client.post(reverse('reenviar_comodato_assinado', args=[self.comodato.id]), {
            'arquivo_assinado': SimpleUploadedFile('termo.pdf', b'%PDF-1.4', content_type='application/pdf'),
        })

        self.comodato.refresh_from_db()
        self.assertEqual(self.comodato.status, 'ativo')

    def test_detalhe_mostra_bloco_de_comodatos_e_badge_na_sidebar(self):
        self.client.force_login(self.user_rh)

        # Em validação sempre tem o termo assinado: é o envio dele que muda o status.
        with self.settings(MEDIA_ROOT=tempfile.mkdtemp()):
            self.comodato.arquivo_assinado = SimpleUploadedFile('assinado.pdf', b'%PDF-1.4')
            self.comodato.save()
            response = self.client.get(self.url_detalhe)

        self.assertContains(response, 'Comodato de Uniforme')
        self.assertContains(response, reverse('aprovar_comodato', args=[self.comodato.id]))
        self.assertEqual(response.context['badge_comodatos'], 1)

    def test_detalhe_lista_so_comodatos_ja_assinados(self):
        self.client.force_login(self.user_rh)
        with self.settings(MEDIA_ROOT=tempfile.mkdtemp()):
            # Gerado, ainda sem assinatura.
            sem_assinatura = TermoComodato.objects.create(
                funcionario=self.funcionario, modalidade='equipamento', data_emprestimo=date(2026, 10, 1),
                status='aguardando_assinatura',
            )
            # Assinado, mas rejeitado: volta a aguardar um novo envio.
            rejeitado = TermoComodato.objects.create(
                funcionario=self.funcionario, modalidade='equipamento', data_emprestimo=date(2026, 10, 1),
                status='aguardando_assinatura', motivo_rejeicao='Ilegível',
                arquivo_assinado=SimpleUploadedFile('velho.pdf', b'%PDF-1.4'),
            )
            ativo = TermoComodato.objects.create(
                funcionario=self.funcionario, modalidade='equipamento', data_emprestimo=date(2026, 10, 1),
                status='ativo', arquivo_assinado=SimpleUploadedFile('ok.pdf', b'%PDF-1.4'),
            )

            response = self.client.get(self.url_detalhe)

        self.assertEqual(list(response.context['comodatos']), [ativo])
        self.assertNotContains(response, reverse('ver_comodato', args=[sem_assinatura.id, 'gerado']))
        self.assertNotContains(response, reverse('ver_comodato', args=[rejeitado.id, 'assinado']))


class VerComodatoViewTests(TestCase):
    """O termo de comodato só abre para o dono, o RH ou o gestor do setor."""

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.override = self.settings(MEDIA_ROOT=self.media)
        self.override.enable()
        self.addCleanup(self.override.disable)

        setor = Setor.objects.create(nome='Produção')
        outro_setor = Setor.objects.create(nome='Expedição')
        self.funcionario = Funcionario.objects.create(
            nome='Colaborador Teste', cpf='12345678901', cargo='Analista', setor=setor, status='ativo',
        )
        self.user_dono = self._usuario('12345678901', self.funcionario, 'colaborador')

        outro = Funcionario.objects.create(nome='Outro', cpf='11111111111', cargo='Analista', setor=setor, status='ativo')
        self.user_outro = self._usuario('11111111111', outro, 'colaborador')

        rh = Funcionario.objects.create(nome='RH', cpf='98765432100', cargo='RH', setor=outro_setor, status='ativo')
        self.user_rh = self._usuario('98765432100', rh, 'rh')

        gestor_fora = Funcionario.objects.create(nome='Gestor', cpf='22222222222', cargo='Gestor', setor=outro_setor, status='ativo')
        self.user_gestor_fora = self._usuario('22222222222', gestor_fora, 'gestor')

        self.comodato = TermoComodato.objects.create(
            funcionario=self.funcionario, modalidade='uniforme', data_emprestimo=date(2026, 10, 1),
        )
        self.comodato.arquivo_assinado.save('termo.pdf', SimpleUploadedFile('termo.pdf', b'%PDF-1.4'), save=True)
        self.url = reverse('ver_comodato', args=[self.comodato.id, 'assinado'])

    def _usuario(self, cpf, funcionario, papel):
        user = User.objects.create_user(username=cpf, password='senha123')
        Usuario.objects.create(user=user, funcionario=funcionario, papel=papel)
        return user

    def _baixar(self, user, url=None):
        self.client.force_login(user)
        response = self.client.get(url or self.url)
        if hasattr(response, 'streaming_content'):
            b''.join(response.streaming_content)
        return response

    def test_dono_e_rh_abrem_o_termo(self):
        for user in (self.user_dono, self.user_rh):
            response = self._baixar(user)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['X-Frame-Options'], 'SAMEORIGIN')

    def test_outro_colaborador_e_gestor_de_outro_setor_nao_abrem(self):
        for user in (self.user_outro, self.user_gestor_fora):
            response = self._baixar(user)
            self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)

    def test_termo_ainda_nao_gerado_da_404(self):
        response = self._baixar(self.user_rh, reverse('ver_comodato', args=[self.comodato.id, 'gerado']))
        self.assertEqual(response.status_code, 404)

    def test_versao_desconhecida_da_404(self):
        response = self._baixar(self.user_rh, reverse('ver_comodato', args=[self.comodato.id, 'qualquer']))
        self.assertEqual(response.status_code, 404)

    def test_rotas_de_acao_nao_sao_capturadas_pela_de_arquivo(self):
        self.assertEqual(reverse('aprovar_comodato', args=[1]), '/comodatos/1/aprovar/')
        from django.urls import resolve
        self.assertEqual(resolve('/comodatos/1/aprovar/').url_name, 'aprovar_comodato')


class GestorVeEquipeDeOutrosSetoresTests(TestCase):
    """
    Gestor responsável por setores em que não está lotado (ex.: gerente de
    produção que responde pela Moagem e pelo Envase) vê a ficha e a lista de
    Funcionários da equipe toda -- a mesma regra do ponto e da folha.
    """

    def setUp(self):
        self.producao = Setor.objects.create(nome='Produção')
        self.moagem = Setor.objects.create(nome='Moagem')
        self.gestora = Funcionario.objects.create(nome='Jô Gerente', cpf='31100000000', cargo='Gerente', setor=self.producao)
        for setor in (self.producao, self.moagem):
            setor.gestor = self.gestora
            setor.save()
        self.user_gestora = User.objects.create_user(username='31100000000', password='senha123')
        Usuario.objects.create(user=self.user_gestora, funcionario=self.gestora, papel='gestor')
        self.moleiro = Funcionario.objects.create(nome='Moleiro da Moagem', cpf='31100000001', cargo='Moleiro', setor=self.moagem)
        self.de_fora = Funcionario.objects.create(
            nome='Caio Financeiro', cpf='31100000002', cargo='Analista', setor=Setor.objects.create(nome='Financeiro'),
        )
        self.client.force_login(self.user_gestora)

    def test_lista_mostra_os_setores_que_ela_responde_sem_ela_mesma(self):
        resposta = self.client.get(reverse('listar_funcionarios'))

        self.assertEqual(list(resposta.context['pagina']), [self.moleiro])

    def test_abre_a_ficha_de_quem_esta_noutro_setor_dela(self):
        self.assertEqual(self.client.get(reverse('detalhe_funcionario', args=[self.moleiro.id])).status_code, 200)
        resposta = self.client.get(reverse('detalhe_funcionario', args=[self.de_fora.id]))
        self.assertRedirects(resposta, reverse('listar_funcionarios'), fetch_redirect_response=False)

    def test_gera_acesso_so_da_equipe(self):
        self.client.post(reverse('gerar_acesso_funcionario', args=[self.moleiro.id]))
        self.client.post(reverse('gerar_acesso_funcionario', args=[self.de_fora.id]))

        self.assertTrue(Usuario.objects.filter(funcionario=self.moleiro).exists())
        self.assertFalse(Usuario.objects.filter(funcionario=self.de_fora).exists())

    def test_ve_documento_da_equipe(self):
        tipo = TipoDocumento.objects.create(nome='RG')
        with self.settings(MEDIA_ROOT=tempfile.mkdtemp()):
            documento, de_fora = (
                Documento.objects.create(
                    funcionario=funcionario, tipo_documento=tipo,
                    arquivo=SimpleUploadedFile('rg.pdf', b'%PDF-1.4 teste', content_type='application/pdf'),
                )
                for funcionario in (self.moleiro, self.de_fora)
            )

            resposta = self.client.get(reverse('ver_documento', args=[documento.id]))
            resposta_de_fora = self.client.get(reverse('ver_documento', args=[de_fora.id]))

            self.assertEqual(resposta.status_code, 200)
            resposta.close()
            self.assertRedirects(resposta_de_fora, reverse('dashboard'), fetch_redirect_response=False)

    def test_rh_com_filtro_meu_setor_continua_vendo_so_o_proprio_setor(self):
        rh = Funcionario.objects.create(nome='Rita RH', cpf='31100000003', cargo='Analista de RH', setor=self.moagem)
        user_rh = User.objects.create_user(username='31100000003', password='senha123')
        Usuario.objects.create(user=user_rh, funcionario=rh, papel='rh')
        self.client.force_login(user_rh)

        resposta = self.client.get(reverse('listar_funcionarios') + '?escopo=meu_setor')

        self.assertCountEqual(resposta.context['pagina'], [self.moleiro, rh])
