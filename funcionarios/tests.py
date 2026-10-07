import io
import tempfile

import openpyxl
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from datetime import date

from .forms import AtestadoForm, MeuPerfilForm, PreCadastroFuncionarioForm
from .models import (
    Atestado, Documento, Funcionario, ItemCatalogoComodato, ItemComodato, Setor, SolicitacaoSenha,
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
