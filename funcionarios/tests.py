import tempfile

import openpyxl
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from .forms import MeuPerfilForm, PreCadastroFuncionarioForm
from .models import Documento, Funcionario, Setor, TipoDocumento, Usuario


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
    """PIS agora é informado pelo próprio funcionário em Meu Perfil, como um campo comum -- não mais um documento."""

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


class AssinaturaGovViewTests(TestCase):
    """Tela "Assinar com Gov.br" -- só link externo + reenvio do contrato assinado, sem API."""

    def setUp(self):
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

    def test_mostra_link_externo_e_estado_vazio_sem_contrato(self):
        response = self.client.get(reverse('minhas_assinaturas'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'https://assinador.iti.br/')
        self.assertContains(response, 'ainda não foi gerado pelo RH')

    def test_mostra_card_de_download_e_reenvio_quando_ha_contrato(self):
        Documento.objects.create(
            funcionario=self.funcionario, tipo_documento=self.tipo_contrato,
            arquivo=self._arquivo(), status='pendente',
        )

        response = self.client.get(reverse('minhas_assinaturas'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Contrato de Trabalho')
        self.assertNotContains(response, 'ainda não foi gerado pelo RH')

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
