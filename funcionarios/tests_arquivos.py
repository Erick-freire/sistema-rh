"""
Arquivo enviado nunca pode abrir como página no navegador (funcionarios/arquivos.py).

Na entrada, só PDF, JPG e PNG de verdade, até 10 MB. Na saída, o tipo vem do
conteúdo: o que não for PDF/JPG/PNG -- arquivo antigo, de antes da validação --
sai como download e com CSP sandbox.
"""
import io
import os
import shutil
import tempfile
from datetime import date
from unittest import mock

from django.contrib.auth.models import User
from django.contrib.messages import get_messages
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.urls import reverse
from PIL import Image

from terceiros.tests import _criar_empresa, _criar_responsavel

from .arquivos import EXTENSOES_IMAGEM, validar_upload
from .forms import OcorrenciaEPIForm
from .models import Documento, FichaEPI, Funcionario, Setor, TermoComodato, TipoDocumento, Usuario

PDF = b'%PDF-1.4\n1 0 obj\n<<>>\nendobj\n'
HTML = b'<html><body><script>fetch("/rh/").then(r => r.text())</script></body></html>'
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'


def _imagem(formato):
    buffer = io.BytesIO()
    Image.new('RGB', (2, 2), 'white').save(buffer, formato)
    return buffer.getvalue()


def _upload(nome, conteudo):
    return SimpleUploadedFile(nome, conteudo)


class MidiaTemporariaMixin:
    """Toda gravação de arquivo vai para uma pasta descartável, nunca para a mídia real."""

    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        override = self.settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)

    def _usuario(self, cpf, funcionario, papel):
        user = User.objects.create_user(username=cpf, password='senha-forte-123')
        Usuario.objects.create(user=user, funcionario=funcionario, papel=papel)
        return user

    def _baixar(self, url):
        resposta = self.client.get(url)
        # Ler até o fim faz o cliente de teste fechar a resposta (e o
        # arquivo). Não chamar close() de novo: fora do cliente, ele dispara o
        # request_finished, que fecha a conexão do banco no meio do teste.
        if hasattr(resposta, 'streaming_content'):
            b''.join(resposta.streaming_content)
        return resposta


class ValidarUploadTests(TestCase):
    def test_aceita_pdf_jpg_e_png_de_verdade(self):
        for nome, conteudo in (
            ('rg.pdf', PDF), ('RG.PDF', PDF), ('foto.jpg', _imagem('JPEG')),
            ('foto.jpeg', _imagem('JPEG')), ('print.png', _imagem('PNG')),
        ):
            with self.subTest(nome=nome):
                arquivo = _upload(nome, conteudo)
                self.assertIs(validar_upload(arquivo), arquivo)

    def test_aceita_pdf_com_bytes_antes_do_cabecalho(self):
        validar_upload(_upload('scanner.pdf', b'\r\n\r\n' + PDF))

    def test_recusa_extensao_fora_da_lista(self):
        for nome, conteudo in (('pagina.html', HTML), ('desenho.svg', SVG), ('planilha.xlsx', b'PK\x03\x04')):
            with self.subTest(nome=nome):
                with self.assertRaises(ValidationError) as contexto:
                    validar_upload(_upload(nome, conteudo))
                self.assertEqual(contexto.exception.code, 'formato')
                self.assertIn('PDF, JPG ou PNG', contexto.exception.messages[0])

    def test_recusa_html_renomeado_para_pdf(self):
        with self.assertRaises(ValidationError) as contexto:
            validar_upload(_upload('rg.pdf', HTML))

        self.assertEqual(contexto.exception.code, 'conteudo')
        self.assertIn('não é um PDF válido', contexto.exception.messages[0])

    def test_recusa_conteudo_de_outro_formato_permitido(self):
        with self.assertRaises(ValidationError) as contexto:
            validar_upload(_upload('rg.pdf', _imagem('PNG')))

        self.assertEqual(contexto.exception.code, 'conteudo')

    def test_recusa_arquivo_vazio(self):
        with self.assertRaises(ValidationError):
            validar_upload(_upload('rg.pdf', b''))

    def test_respeita_o_limite_de_tamanho(self):
        with mock.patch('funcionarios.arquivos.TAMANHO_MAXIMO', len(PDF)):
            validar_upload(_upload('no-limite.pdf', PDF))

            with self.assertRaises(ValidationError) as contexto:
                validar_upload(_upload('grande.pdf', PDF + b'x'))

        self.assertEqual(contexto.exception.code, 'tamanho')
        self.assertIn('limite é 10 MB', contexto.exception.messages[0])

    def test_campo_de_imagem_recusa_pdf_e_gif(self):
        for nome, conteudo in (('foto.pdf', PDF), ('foto.gif', _imagem('GIF'))):
            with self.subTest(nome=nome):
                with self.assertRaises(ValidationError) as contexto:
                    validar_upload(_upload(nome, conteudo), EXTENSOES_IMAGEM)
                self.assertIn('JPG ou PNG', contexto.exception.messages[0])

    def test_arquivo_ja_salvo_nao_e_reconferido(self):
        self.assertIsNone(validar_upload(None))
        ja_salvo = ContentFile(HTML, name='antigo.pdf')
        self.assertIs(validar_upload(ja_salvo), ja_salvo)


class UploadDoColaboradorTests(MidiaTemporariaMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.setor = Setor.objects.create(nome='Produção')
        self.funcionario = Funcionario.objects.create(
            nome='Colaborador Teste', cpf='12345678901', cargo='Analista', setor=self.setor,
            status='ativo', email='antigo@sotrigo.com.br',
        )
        self.user = self._usuario('12345678901', self.funcionario, 'colaborador')
        self.client.force_login(self.user)
        self.rg = TipoDocumento.objects.create(nome='RG', obrigatorio=True, condicao='sempre')

    def test_documento_html_e_recusado(self):
        resposta = self.client.post(reverse('enviar_documento'), {
            'tipo_documento': self.rg.id, 'arquivo': _upload('rg.html', HTML),
        })

        self.assertEqual(Documento.objects.count(), 0)
        self.assertContains(resposta, 'Formato não aceito')

    def test_documento_html_com_nome_de_pdf_e_recusado(self):
        resposta = self.client.post(reverse('enviar_documento'), {
            'tipo_documento': self.rg.id, 'arquivo': _upload('rg.pdf', HTML),
        })

        self.assertEqual(Documento.objects.count(), 0)
        self.assertContains(resposta, 'não é um PDF válido')

    def test_documento_pdf_e_aceito(self):
        self.client.post(reverse('enviar_documento'), {
            'tipo_documento': self.rg.id, 'arquivo': _upload('rg.pdf', PDF),
        })

        self.assertEqual(Documento.objects.count(), 1)

    def test_campo_de_documento_filtra_formatos_no_seletor(self):
        resposta = self.client.get(reverse('enviar_documento'))

        self.assertContains(resposta, 'accept="application/pdf,image/jpeg,image/png,.pdf,.jpg,.jpeg,.png"')

    def test_documento_de_filho_invalido_nao_salva_o_perfil_pela_metade(self):
        certidao = TipoDocumento.objects.create(
            nome='Certidão de nascimento', obrigatorio=True, condicao='filho_menor_idade',
        )
        dados = dict(
            pis='12345678901', sexo='masculino', estado_civil='solteiro',
            email='novo@sotrigo.com.br', banco='Banco X', agencia='0001', numero_conta='12345',
            usa_vale_transporte='True', tem_filho_menor_idade='on',
        )
        dados[f'documento_filho_{certidao.id}'] = _upload('certidao.svg', SVG)

        resposta = self.client.post(reverse('meu_perfil'), dados)

        self.assertContains(resposta, 'Certidão de nascimento: Formato não aceito')
        self.funcionario.refresh_from_db()
        self.assertEqual(self.funcionario.email, 'antigo@sotrigo.com.br')
        self.assertEqual(Documento.objects.count(), 0)

    def test_foto_de_perfil_aceita_png_e_recusa_gif(self):
        self.client.post(reverse('atualizar_foto'), {'foto': _upload('eu.gif', _imagem('GIF'))})
        self.funcionario.refresh_from_db()
        self.assertFalse(self.funcionario.foto)

        self.client.post(reverse('atualizar_foto'), {'foto': _upload('eu.png', _imagem('PNG'))})
        self.funcionario.refresh_from_db()
        self.assertTrue(self.funcionario.foto)

    def test_termo_de_comodato_assinado_em_html_e_recusado(self):
        comodato = TermoComodato.objects.create(
            funcionario=self.funcionario, modalidade='uniforme', data_emprestimo=date(2026, 10, 1),
            status='aguardando_assinatura',
        )

        resposta = self.client.post(
            reverse('reenviar_comodato_assinado', args=[comodato.id]),
            {'arquivo_assinado': _upload('termo.pdf', HTML)},
        )

        comodato.refresh_from_db()
        self.assertEqual(comodato.status, 'aguardando_assinatura')
        self.assertFalse(comodato.arquivo_assinado)
        mensagens = [str(m) for m in get_messages(resposta.wsgi_request)]
        self.assertIn('Termo não enviado. O arquivo não é um PDF válido', mensagens[0])

    def test_ficha_epi_assinada_em_html_e_recusada(self):
        ficha = FichaEPI.objects.create(
            funcionario=self.funcionario, data_entrega=date(2026, 10, 1), status='aguardando_assinatura',
        )
        ficha.arquivo_gerado.save('ficha.pdf', ContentFile(PDF))

        resposta = self.client.post(
            reverse('reenviar_epi_assinado', args=[ficha.id]),
            {'arquivo_assinado': _upload('ficha.html', HTML)},
        )

        ficha.refresh_from_db()
        self.assertEqual(ficha.status, 'aguardando_assinatura')
        self.assertFalse(ficha.arquivo_assinado)
        mensagens = [str(m) for m in get_messages(resposta.wsgi_request)]
        self.assertIn('Ficha não enviada. Formato não aceito', mensagens[0])

    def test_foto_de_ocorrencia_epi_so_aceita_imagem(self):
        form = OcorrenciaEPIForm(
            data={'funcionario': self.funcionario.id, 'data_ocorrencia': '2026-10-01', 'descricao': 'Sem luva'},
            files={'foto': _upload('foto.svg', SVG)},
        )

        self.assertFalse(form.is_valid())
        self.assertIn('foto', form.errors)


class EntregaDeArquivoTests(MidiaTemporariaMixin, TestCase):
    """O tipo da resposta sai do conteúdo: HTML gravado antes da validação vira download."""

    def setUp(self):
        super().setUp()
        setor = Setor.objects.create(nome='RH')
        self.funcionario = Funcionario.objects.create(
            nome='Ana RH', cpf='98765432100', cargo='Analista', setor=setor, status='ativo',
        )
        self.client.force_login(self._usuario('98765432100', self.funcionario, 'rh'))
        self.rg = TipoDocumento.objects.create(nome='RG', obrigatorio=True, condicao='sempre')

    def _documento(self, nome, conteudo):
        return Documento.objects.create(
            funcionario=self.funcionario, tipo_documento=self.rg, arquivo=ContentFile(conteudo, name=nome),
        )

    def _gravar_na_midia(self, caminho, conteudo):
        destino = os.path.join(self.media, *caminho.split('/'))
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, 'wb') as arquivo:
            arquivo.write(conteudo)

    def test_pdf_abre_no_navegador_sem_sandbox(self):
        documento = self._documento('rg.pdf', PDF)

        resposta = self._baixar(reverse('ver_documento', args=[documento.id]))

        self.assertEqual(resposta['Content-Type'], 'application/pdf')
        self.assertTrue(resposta['Content-Disposition'].startswith('inline'))
        # O leitor de PDF do Chrome não abre PDF em sandbox (popup de validação do RH).
        self.assertNotIn('Content-Security-Policy', resposta)

    def test_html_antigo_com_nome_de_pdf_vira_download(self):
        documento = self._documento('rg.pdf', HTML)

        resposta = self._baixar(reverse('ver_documento', args=[documento.id]))

        self.assertEqual(resposta['Content-Type'], 'application/octet-stream')
        self.assertTrue(resposta['Content-Disposition'].startswith('attachment'))
        self.assertEqual(resposta['Content-Security-Policy'], 'sandbox')

    def test_imagem_abre_com_o_tipo_do_conteudo_e_sandbox(self):
        documento = self._documento('rg.pdf', _imagem('PNG'))

        resposta = self._baixar(reverse('ver_documento', args=[documento.id]))

        self.assertEqual(resposta['Content-Type'], 'image/png')
        self.assertEqual(resposta['Content-Security-Policy'], 'sandbox')

    def test_caminho_direto_da_midia_nao_serve_nenhum_arquivo(self):
        """Nem para o RH: todo arquivo sai pela view que confere a permissão."""
        arquivos = {
            'epi/ficha_epi_1_98765432100.pdf': PDF,
            'contratos_terceiros/contrato_11222333000181_1.pdf': PDF,
            'terceiros_documentos/colaboradores/aso.pdf': PDF,
            'assinaturas_pendentes/2026/08/docmedicopaciente.pdf': PDF,
            'fotos_perfil/ana.png': _imagem('PNG'),
            'terceiros_documentos/aso.html': HTML,
        }
        for caminho, conteudo in arquivos.items():
            self._gravar_na_midia(caminho, conteudo)

        for caminho in arquivos:
            with self.subTest(caminho=caminho):
                self.assertEqual(self.client.get(f'/media/{caminho}').status_code, 404)


class FotoDePerfilTests(MidiaTemporariaMixin, TestCase):
    """A foto sai por view própria: o dono e quem opera ele (DP/RH, gestor da equipe)."""

    def setUp(self):
        super().setUp()
        self.producao = Setor.objects.create(nome='Produção')
        self.expedicao = Setor.objects.create(nome='Expedição')
        self.ana = Funcionario.objects.create(
            nome='Ana', cpf='11111111111', cargo='Operadora', setor=self.producao, status='ativo',
            foto=ContentFile(_imagem('PNG'), name='IMG_0238.png'),
        )
        self.url = reverse('foto_funcionario', args=[self.ana.id])

    def _logar(self, cpf, papel, setor):
        funcionario = Funcionario.objects.create(
            nome=f'Pessoa {cpf}', cpf=cpf, cargo='Cargo', setor=setor, status='ativo',
        )
        self.client.force_login(self._usuario(cpf, funcionario, papel))
        return funcionario

    def test_dono_ve_a_propria_foto(self):
        self.client.force_login(self._usuario('11111111111', self.ana, 'colaborador'))

        resposta = self._baixar(self.url)

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta['Content-Type'], 'image/png')
        # Nenhum cache no caminho (Cloudflare) guarda a foto; o navegador revalida.
        self.assertEqual(resposta['Cache-Control'], 'private, no-cache')
        self.assertIn('Last-Modified', resposta)

    def test_rh_e_dp_veem_a_foto_de_qualquer_um(self):
        for cpf, papel in (('22222222222', 'rh'), ('33333333333', 'dp')):
            with self.subTest(papel=papel):
                self._logar(cpf, papel, self.expedicao)
                self.assertEqual(self._baixar(self.url).status_code, 200)

    def test_gestor_ve_a_foto_da_propria_equipe(self):
        self._logar('44444444444', 'gestor', self.producao)

        self.assertEqual(self._baixar(self.url).status_code, 200)

    def test_quem_nao_opera_o_funcionario_nao_ve(self):
        casos = (
            ('55555555555', 'gestor', self.expedicao),
            ('66666666666', 'colaborador', self.producao),
            ('77777777777', 'seguranca_trabalho', self.producao),
        )
        for cpf, papel, setor in casos:
            with self.subTest(papel=papel):
                self._logar(cpf, papel, setor)
                self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_responsavel_de_empresa_terceira_nao_ve(self):
        self.client.force_login(_criar_responsavel(_criar_empresa(status='ativo')).user)

        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_sem_login_vai_para_o_login(self):
        resposta = self.client.get(self.url)

        self.assertEqual(resposta.status_code, 302)
        self.assertIn(reverse('login'), resposta['Location'])

    def test_foto_que_nao_mudou_volta_304_sem_corpo(self):
        self.client.force_login(self._usuario('11111111111', self.ana, 'colaborador'))
        ultima = self._baixar(self.url)['Last-Modified']

        resposta = self.client.get(self.url, HTTP_IF_MODIFIED_SINCE=ultima)

        self.assertEqual(resposta.status_code, 304)
        self.assertEqual(resposta['Cache-Control'], 'private, no-cache')

    def test_funcionario_sem_foto_da_404(self):
        self.ana.foto = None
        self.ana.save()
        self.client.force_login(self._usuario('11111111111', self.ana, 'colaborador'))

        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_foto_antiga_em_html_vira_download(self):
        self.ana.foto.save('foto.png', ContentFile(HTML))
        self.client.force_login(self._usuario('11111111111', self.ana, 'colaborador'))

        resposta = self._baixar(self.url)

        self.assertEqual(resposta['Content-Type'], 'application/octet-stream')
        self.assertTrue(resposta['Content-Disposition'].startswith('attachment'))
        self.assertEqual(resposta['Content-Security-Policy'], 'sandbox')

    def test_telas_apontam_para_a_view_da_foto(self):
        self.client.force_login(self._usuario('11111111111', self.ana, 'colaborador'))

        resposta = self.client.get(reverse('meu_perfil'))

        self.assertContains(resposta, f'src="{self.url}"')
        self.assertNotContains(resposta, '/media/')


class VerificarArquivosEnviadosTests(MidiaTemporariaMixin, TestCase):
    def _gravar(self, caminho, conteudo):
        destino = os.path.join(self.media, *caminho.split('/'))
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, 'wb') as arquivo:
            arquivo.write(conteudo)

    def _rodar(self, *args):
        saida = io.StringIO()
        call_command('verificar_arquivos_enviados', *args, stdout=saida)
        return saida.getvalue()

    def test_lista_html_primeiro_e_ignora_pdf_e_imagem(self):
        self._gravar('documentos/2026/10/rg.pdf', PDF)
        self._gravar('fotos_perfil/ana.jpg', _imagem('JPEG'))
        self._gravar('terceiros_documentos/zz_aso.pdf', HTML)
        self._gravar('terceiros_documentos/aa_lixo.pdf', b'conteudo')

        saida = self._rodar()

        self.assertIn('4 arquivo(s) verificado(s), 2 fora de PDF/JPG/PNG.', saida)
        self.assertIn('1 com conteúdo de página/script', saida)
        self.assertIn('terceiros_documentos: 2 / 2', saida)
        self.assertLess(saida.index('zz_aso.pdf'), saida.index('aa_lixo.pdf'))
        self.assertNotIn('rg.pdf', saida)

    def test_nao_altera_nenhum_arquivo(self):
        self._gravar('terceiros_documentos/aso.pdf', HTML)

        self._rodar()

        with open(os.path.join(self.media, 'terceiros_documentos', 'aso.pdf'), 'rb') as arquivo:
            self.assertEqual(arquivo.read(), HTML)

    def test_recusa_pasta_fora_da_midia(self):
        with self.assertRaises(CommandError):
            self._rodar('--pasta', '..')
