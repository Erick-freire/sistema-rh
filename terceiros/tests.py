import os

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from funcionarios.models import Funcionario, Setor, Usuario

from .forms import CadastroEmpresaForm
from .models import (
    ClausulaContratual,
    ColaboradorTerceiro,
    ContratoTerceiro,
    DocumentoColaborador,
    DocumentoEmpresaTerceira,
    EmpresaTerceira,
    ResponsavelTerceiro,
)


def _dados_empresa(**kwargs):
    dados = dict(
        razao_social='Prestadora Alfa LTDA', nome_fantasia='Alfa', cnpj='11222333000181',
        logradouro='Rua A', numero='100', bairro='Centro', cidade='Anápolis',
        estado='GO', cep='75000000',
        responsavel_nome='Maria Souza', responsavel_cpf='52998224725',
    )
    dados.update(kwargs)
    return dados


def _criar_empresa(status='pendente_cadastro', **kwargs):
    campos = {
        campo: valor for campo, valor in _dados_empresa(**kwargs).items()
        if not campo.startswith('responsavel_')
    }
    return EmpresaTerceira.objects.create(status=status, **campos)


def _criar_responsavel(empresa, completo=True, cpf='52998224725'):
    user = User.objects.create_user(username=cpf, password='senha-forte-123')
    extras = {}
    if completo:
        extras = dict(
            rg='1234567', estado_civil='solteiro', profissao='Engenheira',
            endereco='Rua B, 20, Anápolis/GO',
        )
    return ResponsavelTerceiro.objects.create(
        empresa=empresa, user=user, nome='Maria Souza', cpf=cpf,
        senha_provisoria=False, **extras
    )


def _dados_minha_empresa(**kwargs):
    """
    POST da aba "Minha Empresa": ela grava empresa e representante legal no
    mesmo envio, então o formulário leva os campos dos dois.
    """
    dados = {
        campo: valor for campo, valor in _dados_empresa().items()
        if not campo.startswith('responsavel_')
    }
    dados.update(
        nome='Maria Souza', rg='1234567', estado_civil='solteiro',
        profissao='Engenheira', endereco='Rua B, 20',
    )
    dados.update(kwargs)
    return dados


def _criar_usuario_rh():
    setor = Setor.objects.create(nome='RH')
    funcionario = Funcionario.objects.create(
        nome='Ana RH', cargo='Analista', setor=setor, cpf='11122233396', status='ativo',
    )
    user = User.objects.create_user(username='11122233396', password='senha-forte-123')
    Usuario.objects.create(user=user, funcionario=funcionario, papel='rh')
    return user


def _arquivo(nome='doc.pdf'):
    return SimpleUploadedFile(nome, b'conteudo-do-arquivo', content_type='application/pdf')


class CadastroEmpresaFormTests(TestCase):
    def test_cadastro_valido_normaliza_cnpj_e_cpf(self):
        form = CadastroEmpresaForm(_dados_empresa(cnpj='11.222.333/0001-81', cep='75.000-000'))

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['cnpj'], '11222333000181')
        self.assertEqual(form.cleaned_data['cep'], '75000000')

    def test_cnpj_duplicado_da_erro_amigavel(self):
        _criar_empresa()

        form = CadastroEmpresaForm(_dados_empresa(responsavel_cpf='39053344705'))

        self.assertFalse(form.is_valid())
        self.assertIn('Prestadora Alfa', str(form.errors['cnpj']))

    def test_cnpj_com_digitos_insuficientes_e_recusado(self):
        form = CadastroEmpresaForm(_dados_empresa(cnpj='112223330001'))

        self.assertFalse(form.is_valid())
        self.assertIn('cnpj', form.errors)

    def test_cpf_ja_usado_como_login_e_recusado(self):
        User.objects.create_user(username='52998224725', password='x')

        form = CadastroEmpresaForm(_dados_empresa())

        self.assertFalse(form.is_valid())
        self.assertIn('responsavel_cpf', form.errors)


class FluxoStatusTests(TestCase):
    """As transições de status seguem o desenho do onboarding de CLT."""

    def setUp(self):
        self.rh = _criar_usuario_rh()

    def test_cadastro_pelo_rh_cria_usuario_e_nasce_pendente(self):
        self.client.force_login(self.rh)

        resposta = self.client.post(reverse('terceiros:cadastrar_empresa'), _dados_empresa())

        self.assertEqual(resposta.status_code, 200)
        empresa = EmpresaTerceira.objects.get(cnpj='11222333000181')
        self.assertEqual(empresa.status, 'pendente_cadastro')
        self.assertTrue(empresa.responsavel.senha_provisoria)
        self.assertEqual(empresa.responsavel.user.username, '52998224725')

    def test_responsavel_completa_os_dados_e_libera_a_geracao_do_contrato(self):
        empresa = _criar_empresa()
        responsavel = _criar_responsavel(empresa, completo=False)
        self.client.force_login(responsavel.user)

        self.client.post(reverse('terceiros:minha_empresa'), _dados_minha_empresa())

        empresa.refresh_from_db()
        self.assertEqual(empresa.status, 'aguardando_contrato')

    def test_dados_incompletos_nao_avancam_o_status(self):
        empresa = _criar_empresa()
        responsavel = _criar_responsavel(empresa, completo=False)
        self.client.force_login(responsavel.user)

        self.client.post(reverse('terceiros:minha_empresa'), _dados_minha_empresa(rg=''))

        empresa.refresh_from_db()
        self.assertEqual(empresa.status, 'pendente_cadastro')

    def test_enviar_para_validacao_exige_documentacao_completa(self):
        empresa = _criar_empresa(status='aguardando_assinatura')
        responsavel = _criar_responsavel(empresa)
        colaborador = ColaboradorTerceiro.objects.create(
            empresa=empresa, nome='João Silva', cpf='39053344705',
        )
        DocumentoColaborador.objects.create(
            colaborador=colaborador, tipo='selfie', arquivo=_arquivo(),
        )
        self.client.force_login(responsavel.user)

        self.client.post(reverse('terceiros:enviar_para_validacao'))

        empresa.refresh_from_db()
        self.assertEqual(empresa.status, 'aguardando_assinatura')

    def test_enviar_para_validacao_com_tudo_enviado(self):
        empresa = _criar_empresa(status='aguardando_assinatura')
        responsavel = _criar_responsavel(empresa)
        colaborador = ColaboradorTerceiro.objects.create(
            empresa=empresa, nome='João Silva', cpf='39053344705',
        )
        for tipo in DocumentoColaborador.TIPOS_OBRIGATORIOS_ONBOARDING:
            DocumentoColaborador.objects.create(
                colaborador=colaborador, tipo=tipo, arquivo=_arquivo(),
            )
        self.client.force_login(responsavel.user)

        self.client.post(reverse('terceiros:enviar_para_validacao'))

        empresa.refresh_from_db()
        self.assertEqual(empresa.status, 'em_validacao')

    def test_validar_empresa_exige_todos_aprovados(self):
        empresa = _criar_empresa(status='em_validacao')
        _criar_responsavel(empresa)
        colaborador = ColaboradorTerceiro.objects.create(
            empresa=empresa, nome='João Silva', cpf='39053344705',
        )
        for tipo in DocumentoColaborador.TIPOS_OBRIGATORIOS_ONBOARDING:
            DocumentoColaborador.objects.create(
                colaborador=colaborador, tipo=tipo, arquivo=_arquivo(),
            )
        self.client.force_login(self.rh)

        self.client.post(reverse('terceiros:validar_empresa', args=[empresa.id]))
        empresa.refresh_from_db()
        self.assertEqual(empresa.status, 'em_validacao')

        colaborador.documentos.update(status='aprovado')
        self.client.post(reverse('terceiros:validar_empresa', args=[empresa.id]))
        empresa.refresh_from_db()
        self.assertEqual(empresa.status, 'ativo')

    def test_rejeicao_devolve_empresa_para_reenvio(self):
        empresa = _criar_empresa(status='em_validacao')
        _criar_responsavel(empresa)
        colaborador = ColaboradorTerceiro.objects.create(
            empresa=empresa, nome='João Silva', cpf='39053344705',
        )
        documento = DocumentoColaborador.objects.create(
            colaborador=colaborador, tipo='selfie', arquivo=_arquivo(),
        )
        self.client.force_login(self.rh)

        self.client.post(
            reverse('terceiros:rejeitar_documento', args=[documento.id]),
            {'motivo': 'Foto ilegível.'},
        )

        documento.refresh_from_db()
        empresa.refresh_from_db()
        self.assertEqual(documento.status, 'rejeitado')
        self.assertEqual(documento.motivo_rejeicao, 'Foto ilegível.')
        self.assertEqual(documento.analisado_por, self.rh)
        self.assertEqual(empresa.status, 'aguardando_assinatura')


class AnaliseDeDocumentosTests(TestCase):
    """Aprovação e reprovação de documento da equipe, feitas pelo RH."""

    def setUp(self):
        self.rh = _criar_usuario_rh()
        self.empresa = _criar_empresa(status='aguardando_assinatura')
        _criar_responsavel(self.empresa)
        self.colaborador = ColaboradorTerceiro.objects.create(
            empresa=self.empresa, nome='João Silva', cpf='39053344705',
        )
        self.documento = DocumentoColaborador.objects.create(
            colaborador=self.colaborador, tipo='selfie', arquivo=_arquivo(),
        )
        self.client.force_login(self.rh)

    def test_aprova_antes_de_a_prestadora_fechar_o_lote(self):
        self.client.post(reverse('terceiros:aprovar_documento', args=[self.documento.id]))

        self.documento.refresh_from_db()
        self.empresa.refresh_from_db()
        self.assertEqual(self.documento.status, 'aprovado')
        self.assertEqual(self.documento.analisado_por, self.rh)
        # Aprovar não empurra o cadastro adiante -- quem fecha o lote é a prestadora.
        self.assertEqual(self.empresa.status, 'aguardando_assinatura')

    def test_reprova_com_motivo_sem_mexer_no_status_de_quem_ainda_esta_enviando(self):
        self.client.post(
            reverse('terceiros:rejeitar_documento', args=[self.documento.id]),
            {'motivo': 'Foto ilegível.'},
        )

        self.documento.refresh_from_db()
        self.empresa.refresh_from_db()
        self.assertEqual(self.documento.status, 'rejeitado')
        self.assertEqual(self.documento.motivo_rejeicao, 'Foto ilegível.')
        self.assertEqual(self.empresa.status, 'aguardando_assinatura')

    def test_reprovacao_sem_motivo_nao_altera_o_documento(self):
        self.client.post(
            reverse('terceiros:rejeitar_documento', args=[self.documento.id]),
            {'motivo': '   '},
        )

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.status, 'pendente')

    def test_empresa_validada_nao_aceita_mais_analise(self):
        self.empresa.status = 'ativo'
        self.empresa.save()

        self.client.post(reverse('terceiros:aprovar_documento', args=[self.documento.id]))

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.status, 'pendente')

    def test_tela_do_rh_traz_os_botoes_de_analise(self):
        resposta = self.client.get(
            reverse('terceiros:detalhe_empresa', args=[self.empresa.id])
        )

        self.assertContains(
            resposta, reverse('terceiros:aprovar_documento', args=[self.documento.id])
        )
        # O motivo é digitado no modal (data-motivo), não num formulário aberto na página.
        self.assertContains(resposta, 'data-motivo=')

    def test_analise_fica_no_popup_do_olho(self):
        resposta = self.client.get(
            reverse('terceiros:detalhe_empresa', args=[self.empresa.id])
        )

        self.assertContains(resposta, 'id="modal-termo"')
        # Só o ícone de olho leva as ações; não há mais Aprovar/Reprovar na linha.
        self.assertContains(
            resposta, f'data-aprovar="{reverse("terceiros:aprovar_documento", args=[self.documento.id])}"', count=1,
        )
        self.assertNotContains(resposta, f'action="{reverse("terceiros:aprovar_documento", args=[self.documento.id])}"')
        # A mensagem do motivo fala com a prestadora, não com o colaborador CLT.
        self.assertContains(resposta, 'data-rejeitar-mensagem="Escreva o que a prestadora precisa corrigir.')

        arquivo = self.client.get(reverse('terceiros:ver_documento', args=[self.documento.id]))
        self.assertEqual(arquivo['X-Frame-Options'], 'SAMEORIGIN')

    def test_aprovar_exige_post(self):
        self.client.get(reverse('terceiros:aprovar_documento', args=[self.documento.id]))

        self.documento.refresh_from_db()
        self.assertEqual(self.documento.status, 'pendente')

    def test_quadro_de_andamento_some_com_a_empresa_ativa(self):
        url = reverse('terceiros:detalhe_empresa', args=[self.empresa.id])
        self.assertContains(self.client.get(url), 'Onde este cadastro está')

        self.empresa.status = 'ativo'
        self.empresa.save()
        self.assertNotContains(self.client.get(url), 'Onde este cadastro está')

    def test_documento_ja_analisado_nao_mostra_mais_os_botoes(self):
        self.documento.status = 'aprovado'
        self.documento.save()

        resposta = self.client.get(
            reverse('terceiros:detalhe_empresa', args=[self.empresa.id])
        )

        self.assertNotContains(
            resposta, reverse('terceiros:aprovar_documento', args=[self.documento.id])
        )


class ContratoTerceiroTests(TestCase):
    def setUp(self):
        self.rh = _criar_usuario_rh()
        self.empresa = _criar_empresa(status='aguardando_contrato')
        self.responsavel = _criar_responsavel(self.empresa)

    def test_gerar_contrato_produz_pdf_e_avanca_status(self):
        self.client.force_login(self.rh)
        clausula = ClausulaContratual.objects.create(
            titulo='Do Foro', texto='Fica eleito o foro de {{cidade}}.', ordem_sugerida=10,
        )

        resposta = self.client.post(
            reverse('terceiros:gerar_contrato', args=[self.empresa.id]),
            {
                'objeto_servico': 'Manutenção elétrica predial.',
                'composicao_equipe': 'Dois eletricistas e um encarregado.',
                'condicoes_remuneracao': 'R$ 90,00 por hora técnica.',
                'prazo_meses': 12,
                'clausulas_biblioteca': [clausula.id],
                'clausulas_avulsas': 'Cláusula específica de acesso à área fria.',
            },
        )

        self.assertRedirects(
            resposta, reverse('terceiros:detalhe_empresa', args=[self.empresa.id])
        )
        contrato = ContratoTerceiro.objects.get(empresa=self.empresa)
        self.assertTrue(contrato.arquivo_gerado)
        self.assertTrue(contrato.arquivo_gerado.read().startswith(b'%PDF'))
        self.assertEqual(contrato.gerado_por, self.rh)
        self.empresa.refresh_from_db()
        self.assertEqual(self.empresa.status, 'aguardando_assinatura')

    def test_contrato_bloqueado_enquanto_responsavel_nao_completa_dados(self):
        self.responsavel.rg = ''
        self.responsavel.save()
        self.client.force_login(self.rh)

        resposta = self.client.get(reverse('terceiros:gerar_contrato', args=[self.empresa.id]))

        self.assertRedirects(
            resposta, reverse('terceiros:detalhe_empresa', args=[self.empresa.id])
        )
        self.assertFalse(ContratoTerceiro.objects.exists())

    def test_salvar_clausula_avulsa_na_biblioteca(self):
        self.client.force_login(self.rh)

        self.client.post(
            reverse('terceiros:gerar_contrato', args=[self.empresa.id]),
            {
                'objeto_servico': 'Serviço.', 'composicao_equipe': 'Equipe.',
                'condicoes_remuneracao': 'Valor.', 'prazo_meses': 6,
                'clausulas_avulsas': 'Texto reaproveitável.',
                'salvar_avulsas_na_biblioteca': 'on',
                'titulo_avulsas': 'Do Acesso à Área Fria',
            },
        )

        clausula = ClausulaContratual.objects.get(titulo='Do Acesso à Área Fria')
        self.assertEqual(clausula.texto, 'Texto reaproveitável.')

    def test_marcadores_sao_substituidos_no_texto_da_clausula(self):
        clausula = ClausulaContratual.objects.create(
            titulo='Teste',
            texto='Contratada {{razao_social}}, CNPJ {{cnpj}}, por {{nome_prestador}}.',
        )

        from .views import _valores_marcadores
        paragrafos = clausula.paragrafos_renderizados(
            _valores_marcadores(self.empresa, self.responsavel, 12)
        )

        self.assertEqual(
            paragrafos,
            ['Contratada Prestadora Alfa LTDA, CNPJ 11.222.333/0001-81, por Maria Souza.'],
        )

    def test_termo_de_confidencialidade_vai_junto_no_html_gerado(self):
        contrato = ContratoTerceiro.objects.create(
            empresa=self.empresa, objeto_servico='Serviço.', composicao_equipe='Equipe.',
            condicoes_remuneracao='Valor.', prazo_meses=12,
        )

        from django.template.loader import render_to_string
        from .views import montar_clausulas
        html = render_to_string('terceiros/contrato_terceiro.html', {
            'empresa': self.empresa, 'responsavel': self.responsavel, 'contrato': contrato,
            'clausulas': montar_clausulas(contrato, self.empresa, self.responsavel),
            'data_extenso': '1 de janeiro de 2026',
        })

        self.assertIn('TERMO DE COMPROMISSO DE CONFIDENCIALIDADE', html)
        self.assertIn('Cláusula Primeira:', html)

    def test_qualificacao_da_contratante_e_fixa_e_completa(self):
        """
        A abertura do contrato é o texto do modelo em papel, com o sócio
        administrador nomeado. Nada de marcador para preencher à mão depois de
        gerar o PDF.
        """
        contrato = ContratoTerceiro.objects.create(
            empresa=self.empresa, objeto_servico='Serviço.', composicao_equipe='Equipe.',
            condicoes_remuneracao='Valor.', prazo_meses=12,
        )

        from django.template.loader import render_to_string
        from .views import montar_clausulas
        html = render_to_string('terceiros/contrato_terceiro.html', {
            'empresa': self.empresa, 'responsavel': self.responsavel, 'contrato': contrato,
            'clausulas': montar_clausulas(contrato, self.empresa, self.responsavel),
            'data_extenso': '1 de janeiro de 2026',
        })

        self.assertIn('SOTRIGO SOCIEDADE TRITÍCOLA DE GOIÁS LTDA', html)
        self.assertIn('RICARDO ARAÚJO MOURA', html)
        self.assertIn('3251758 SSP/GO', html)
        self.assertIn('261.279.508-13', html)
        for marcador in ('[NOME DO SÓCIO ADMINISTRADOR]', '[RG]', '[CPF]'):
            self.assertNotIn(marcador, html)

    def test_numeracao_das_clausulas_e_sequencial(self):
        contrato = ContratoTerceiro.objects.create(
            empresa=self.empresa, objeto_servico='Serviço.', composicao_equipe='Equipe.',
            condicoes_remuneracao='Valor.', prazo_meses=12,
            clausulas_avulsas='Avulsa um.\nAvulsa dois.',
        )

        from .views import montar_clausulas
        clausulas = montar_clausulas(contrato, self.empresa, self.responsavel)

        # objeto, equipe, remuneração, prazo + duas avulsas
        self.assertEqual(len(clausulas), 6)
        self.assertEqual(clausulas[0]['titulo'], 'Do Objeto')
        self.assertEqual(clausulas[4]['titulo'], 'Das Disposições Específicas')
        self.assertIsNone(clausulas[5]['titulo'])


class BibliotecaDeClausulasTests(TestCase):
    def setUp(self):
        self.rh = _criar_usuario_rh()
        self.clausula = ClausulaContratual.objects.create(
            titulo='Do Sigilo', texto='Texto antigo.', ordem_sugerida=10,
        )
        self.client.force_login(self.rh)

    def _url(self):
        return reverse('terceiros:editar_clausula', args=[self.clausula.id])

    def _campos(self, **kwargs):
        dados = {
            'titulo': 'Do Sigilo e da Confidencialidade',
            'texto': 'Texto novo.',
            'ordem_sugerida': 15,
            'ativa': 'on',
        }
        dados.update(kwargs)
        return {f'clausula{self.clausula.id}-{campo}': valor for campo, valor in dados.items()}

    def test_editar_altera_a_clausula_da_biblioteca(self):
        resposta = self.client.post(self._url(), self._campos())

        self.assertRedirects(resposta, reverse('terceiros:listar_clausulas'))
        self.clausula.refresh_from_db()
        self.assertEqual(self.clausula.titulo, 'Do Sigilo e da Confidencialidade')
        self.assertEqual(self.clausula.texto, 'Texto novo.')
        self.assertEqual(self.clausula.ordem_sugerida, 15)

    def test_edicao_invalida_devolve_o_formulario_preenchido(self):
        resposta = self.client.post(self._url(), self._campos(titulo=''))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Texto novo.')
        self.clausula.refresh_from_db()
        self.assertEqual(self.clausula.texto, 'Texto antigo.')

    def test_edicao_por_get_so_volta_para_a_lista(self):
        resposta = self.client.get(self._url())

        self.assertRedirects(resposta, reverse('terceiros:listar_clausulas'))

    def test_responsavel_nao_edita_clausula(self):
        empresa = _criar_empresa()
        responsavel = _criar_responsavel(empresa)
        self.client.force_login(responsavel.user)

        resposta = self.client.post(self._url(), self._campos())

        self.assertNotEqual(resposta.status_code, 200)
        self.clausula.refresh_from_db()
        self.assertEqual(self.clausula.texto, 'Texto antigo.')


class EquipeDoPrestadorTests(TestCase):
    """Edição e remoção de pessoas da equipe, feitas pelo próprio responsável."""

    def setUp(self):
        self.empresa = _criar_empresa(status='aguardando_assinatura')
        self.responsavel = _criar_responsavel(self.empresa)
        self.colaborador = ColaboradorTerceiro.objects.create(
            empresa=self.empresa, nome='João Silva', cpf='39053344705', funcao='Mecânico',
        )
        self.client.force_login(self.responsavel.user)

    def test_editar_corrige_os_dados_da_pessoa(self):
        resposta = self.client.post(
            reverse('terceiros:editar_colaborador', args=[self.colaborador.id]),
            {'nome': 'João da Silva', 'cpf': '390.533.447-05', 'funcao': 'Eletricista'},
        )

        self.assertRedirects(resposta, reverse('terceiros:funcionarios'))
        self.colaborador.refresh_from_db()
        self.assertEqual(self.colaborador.nome, 'João da Silva')
        self.assertEqual(self.colaborador.cpf, '39053344705')
        self.assertEqual(self.colaborador.funcao, 'Eletricista')

    def test_editar_nao_aceita_cpf_de_outra_pessoa_da_mesma_equipe(self):
        ColaboradorTerceiro.objects.create(
            empresa=self.empresa, nome='Maria Lima', cpf='52998224725',
        )

        resposta = self.client.post(
            reverse('terceiros:editar_colaborador', args=[self.colaborador.id]),
            {'nome': 'João Silva', 'cpf': '52998224725', 'funcao': ''},
        )

        self.assertEqual(resposta.status_code, 200)
        self.colaborador.refresh_from_db()
        self.assertEqual(self.colaborador.cpf, '39053344705')

    def test_remover_apaga_a_pessoa_e_os_arquivos_dos_documentos(self):
        documento = DocumentoColaborador.objects.create(
            colaborador=self.colaborador, tipo='selfie', arquivo=_arquivo(),
        )
        caminho = documento.arquivo.path

        resposta = self.client.post(
            reverse('terceiros:remover_colaborador', args=[self.colaborador.id])
        )

        self.assertRedirects(resposta, reverse('terceiros:funcionarios'))
        self.assertFalse(ColaboradorTerceiro.objects.filter(id=self.colaborador.id).exists())
        self.assertFalse(DocumentoColaborador.objects.filter(id=documento.id).exists())
        self.assertFalse(os.path.exists(caminho))

    def test_remover_por_get_nao_apaga_nada(self):
        resposta = self.client.get(
            reverse('terceiros:remover_colaborador', args=[self.colaborador.id])
        )

        self.assertRedirects(resposta, reverse('terceiros:funcionarios'))
        self.assertTrue(ColaboradorTerceiro.objects.filter(id=self.colaborador.id).exists())

    def test_equipe_travada_antes_do_contrato(self):
        self.empresa.status = 'aguardando_contrato'
        self.empresa.save()

        resposta = self.client.post(
            reverse('terceiros:remover_colaborador', args=[self.colaborador.id])
        )

        self.assertRedirects(resposta, reverse('terceiros:funcionarios'))
        self.assertTrue(ColaboradorTerceiro.objects.filter(id=self.colaborador.id).exists())

    def test_lista_so_mostra_as_acoes_quando_a_equipe_esta_liberada(self):
        url_editar = reverse('terceiros:editar_colaborador', args=[self.colaborador.id])

        self.assertContains(self.client.get(reverse('terceiros:funcionarios')), url_editar)

        self.empresa.status = 'aguardando_contrato'
        self.empresa.save()

        self.assertNotContains(self.client.get(reverse('terceiros:funcionarios')), url_editar)

    def test_responsavel_nao_mexe_na_equipe_de_outra_prestadora(self):
        outra = _criar_empresa(
            status='aguardando_assinatura', cnpj='99888777000166',
            razao_social='Prestadora Beta LTDA',
        )
        outro_responsavel = _criar_responsavel(outra, cpf='11144477735')
        self.client.force_login(outro_responsavel.user)

        for nome in ('terceiros:editar_colaborador', 'terceiros:remover_colaborador'):
            with self.subTest(rota=nome):
                resposta = self.client.post(
                    reverse(nome, args=[self.colaborador.id]),
                    {'nome': 'Invadido', 'cpf': '39053344705', 'funcao': ''},
                )
                self.assertEqual(resposta.status_code, 404)

        self.colaborador.refresh_from_db()
        self.assertEqual(self.colaborador.nome, 'João Silva')


class PermissoesTests(TestCase):
    def setUp(self):
        self.empresa = _criar_empresa(status='aguardando_assinatura')
        self.responsavel = _criar_responsavel(self.empresa)
        self.colaborador = ColaboradorTerceiro.objects.create(
            empresa=self.empresa, nome='João Silva', cpf='39053344705',
        )
        self.documento = DocumentoColaborador.objects.create(
            colaborador=self.colaborador, tipo='selfie', arquivo=_arquivo(),
        )

        self.outra_empresa = _criar_empresa(
            status='aguardando_assinatura', cnpj='99888777000166',
            razao_social='Prestadora Beta LTDA',
        )
        self.outro_responsavel = _criar_responsavel(self.outra_empresa, cpf='39053344705')

    def test_responsavel_nao_ve_documento_de_outra_prestadora(self):
        self.client.force_login(self.outro_responsavel.user)

        resposta = self.client.get(
            reverse('terceiros:ver_documento', args=[self.documento.id])
        )

        # O dashboard, por sua vez, redireciona o responsável para o painel dele.
        self.assertRedirects(resposta, reverse('dashboard'), target_status_code=302)

    def test_responsavel_baixa_documento_da_propria_empresa(self):
        self.client.force_login(self.responsavel.user)

        resposta = self.client.get(
            reverse('terceiros:ver_documento', args=[self.documento.id])
        )

        self.assertEqual(resposta.status_code, 200)

    def test_responsavel_nao_envia_documento_para_colaborador_de_outra_empresa(self):
        self.client.force_login(self.outro_responsavel.user)

        resposta = self.client.post(
            reverse('terceiros:detalhe_colaborador', args=[self.colaborador.id]),
            {'tipo': 'selfie', 'arquivo': _arquivo()},
        )

        self.assertEqual(resposta.status_code, 404)

    def test_responsavel_nao_acessa_telas_de_rh(self):
        self.client.force_login(self.responsavel.user)

        resposta = self.client.get(reverse('terceiros:listar_empresas'))

        self.assertRedirects(resposta, reverse('dashboard'), target_status_code=302)

    def test_colaborador_clt_nao_acessa_telas_de_rh_de_terceiros(self):
        setor = Setor.objects.create(nome='Produção')
        funcionario = Funcionario.objects.create(
            nome='Pedro', cargo='Operador', setor=setor, cpf='11144477735', status='ativo',
        )
        user = User.objects.create_user(username='11144477735', password='senha-forte-123')
        Usuario.objects.create(user=user, funcionario=funcionario, papel='colaborador')
        self.client.force_login(user)

        resposta = self.client.get(reverse('terceiros:listar_empresas'))

        self.assertRedirects(resposta, reverse('dashboard'))

    def test_colaborador_clt_nao_acessa_painel_do_prestador(self):
        setor = Setor.objects.create(nome='Produção')
        funcionario = Funcionario.objects.create(
            nome='Pedro', cargo='Operador', setor=setor, cpf='11144477735', status='ativo',
        )
        user = User.objects.create_user(username='11144477735', password='senha-forte-123')
        Usuario.objects.create(user=user, funcionario=funcionario, papel='colaborador')
        self.client.force_login(user)

        resposta = self.client.get(reverse('terceiros:painel'))

        self.assertRedirects(resposta, reverse('dashboard'))


class ContaParalelaTests(TestCase):
    """Login, senha provisória e dashboard funcionando para os dois tipos de conta."""

    def test_dashboard_manda_responsavel_para_o_painel_dele(self):
        empresa = _criar_empresa()
        responsavel = _criar_responsavel(empresa)
        self.client.force_login(responsavel.user)

        resposta = self.client.get(reverse('dashboard'))

        self.assertRedirects(resposta, reverse('terceiros:painel'))

    def test_senha_provisoria_obriga_troca_antes_do_painel(self):
        empresa = _criar_empresa()
        responsavel = _criar_responsavel(empresa)
        responsavel.senha_provisoria = True
        responsavel.save()
        self.client.force_login(responsavel.user)

        resposta = self.client.get(reverse('terceiros:painel'))

        self.assertRedirects(resposta, reverse('trocar_senha'))

    def test_tela_de_trocar_senha_renderiza_para_o_responsavel(self):
        """
        O `funcionarios/base.html` só monta o shell quando existe
        `usuario_atual`, e o responsável terceiro não tem `Usuario` -- sem um
        template próprio a tela saía em branco e o primeiro acesso ficava
        travado, já que o middleware obriga a passar por aqui.
        """
        empresa = _criar_empresa()
        responsavel = _criar_responsavel(empresa)
        responsavel.senha_provisoria = True
        responsavel.save()
        self.client.force_login(responsavel.user)

        resposta = self.client.get(reverse('trocar_senha'))

        self.assertEqual(resposta.status_code, 200)
        self.assertTemplateUsed(resposta, 'terceiros/trocar_senha.html')
        self.assertContains(resposta, 'Defina sua senha')
        self.assertContains(resposta, 'name="new_password1"')
        self.assertContains(resposta, 'Portal do Prestador')
        self.assertContains(resposta, responsavel.nome)

    def test_tela_de_trocar_senha_renderiza_para_colaborador_clt(self):
        setor = Setor.objects.create(nome='Produção')
        funcionario = Funcionario.objects.create(
            nome='Pedro', cargo='Operador', setor=setor, cpf='11144477735', status='ativo',
        )
        user = User.objects.create_user(username='11144477735', password='senha-forte-123')
        Usuario.objects.create(user=user, funcionario=funcionario, papel='colaborador')
        self.client.force_login(user)

        resposta = self.client.get(reverse('trocar_senha'))

        self.assertEqual(resposta.status_code, 200)
        self.assertTemplateUsed(resposta, 'funcionarios/trocar_senha.html')
        self.assertContains(resposta, 'name="new_password1"')

    def test_trocar_senha_desliga_flag_do_responsavel(self):
        empresa = _criar_empresa()
        responsavel = _criar_responsavel(empresa)
        responsavel.senha_provisoria = True
        responsavel.save()
        self.client.force_login(responsavel.user)

        self.client.post(reverse('trocar_senha'), {
            'new_password1': 'Prestador@2026', 'new_password2': 'Prestador@2026',
        })

        responsavel.refresh_from_db()
        self.assertFalse(responsavel.senha_provisoria)

    def test_login_do_responsavel_chega_no_painel(self):
        empresa = _criar_empresa()
        _criar_responsavel(empresa)

        resposta = self.client.post(
            reverse('login'),
            {'username': '52998224725', 'password': 'senha-forte-123'},
            follow=True,
        )

        self.assertEqual(resposta.redirect_chain[-1][0], reverse('terceiros:painel'))


class DocumentoColaboradorTests(TestCase):
    def setUp(self):
        self.empresa = _criar_empresa(status='aguardando_assinatura')
        self.responsavel = _criar_responsavel(self.empresa)
        self.colaborador = ColaboradorTerceiro.objects.create(
            empresa=self.empresa, nome='João Silva', cpf='39053344705',
        )

    def test_reenvio_substitui_o_arquivo_e_volta_para_analise(self):
        documento = DocumentoColaborador.objects.create(
            colaborador=self.colaborador, tipo='selfie', arquivo=_arquivo('primeira.pdf'),
            status='rejeitado', motivo_rejeicao='Ilegível.',
        )

        DocumentoColaborador.registrar_envio(
            colaborador=self.colaborador, tipo='selfie',
            arquivo=_arquivo('segunda.pdf'), enviado_por=self.responsavel.user,
        )

        self.assertEqual(self.colaborador.documentos.count(), 1)
        documento.refresh_from_db()
        self.assertEqual(documento.status, 'pendente')
        self.assertEqual(documento.motivo_rejeicao, '')
        self.assertIn('segunda', documento.arquivo.name)

    def test_tipos_pendentes_ignora_rejeitado(self):
        DocumentoColaborador.objects.create(
            colaborador=self.colaborador, tipo='selfie', arquivo=_arquivo(), status='rejeitado',
        )

        pendentes = dict(self.colaborador.tipos_pendentes())

        self.assertIn('selfie', pendentes)
        self.assertFalse(self.colaborador.documentacao_completa())

    def test_cpf_duplicado_na_mesma_empresa_e_recusado(self):
        self.client.force_login(self.responsavel.user)

        resposta = self.client.post(reverse('terceiros:cadastrar_colaborador'), {
            'nome': 'Outro Nome', 'cpf': '390.533.447-05', 'funcao': 'Ajudante',
        })

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(self.empresa.colaboradores.count(), 1)


class RenderizacaoDasTelasTests(TestCase):
    """
    Abre cada tela do módulo e confere que ela renderiza.

    Erro de sintaxe em template ou nome de url trocado só aparece em tempo de
    execução -- estes testes são a rede contra isso.
    """

    def setUp(self):
        self.rh = _criar_usuario_rh()
        self.empresa = _criar_empresa(status='em_validacao')
        self.responsavel = _criar_responsavel(self.empresa)
        self.colaborador = ColaboradorTerceiro.objects.create(
            empresa=self.empresa, nome='João Silva', cpf='39053344705', funcao='Eletricista',
        )
        self.documento = DocumentoColaborador.objects.create(
            colaborador=self.colaborador, tipo='selfie', arquivo=_arquivo(),
        )
        self.contrato = ContratoTerceiro.objects.create(
            empresa=self.empresa, objeto_servico='Serviço.', composicao_equipe='Equipe.',
            condicoes_remuneracao='Valor.', prazo_meses=12,
        )

    def test_telas_do_rh(self):
        self.client.force_login(self.rh)

        for nome, args in [
            ('terceiros:listar_empresas', []),
            ('terceiros:cadastrar_empresa', []),
            ('terceiros:listar_clausulas', []),
            ('terceiros:detalhe_empresa', [self.empresa.id]),
        ]:
            with self.subTest(tela=nome):
                resposta = self.client.get(reverse(nome, args=args))
                self.assertEqual(resposta.status_code, 200)

    def test_tela_de_gerar_contrato(self):
        self.empresa.status = 'aguardando_contrato'
        self.empresa.save()
        self.client.force_login(self.rh)

        resposta = self.client.get(
            reverse('terceiros:gerar_contrato', args=[self.empresa.id])
        )

        self.assertEqual(resposta.status_code, 200)

    def test_telas_do_responsavel(self):
        self.client.force_login(self.responsavel.user)

        for nome, args in [
            ('terceiros:painel', []),
            ('terceiros:minha_empresa', []),
            ('terceiros:documentacao_mensal', []),
            ('terceiros:funcionarios', []),
            ('terceiros:detalhe_colaborador', [self.colaborador.id]),
        ]:
            with self.subTest(tela=nome):
                resposta = self.client.get(reverse(nome, args=args))
                self.assertEqual(resposta.status_code, 200)
                # O shell do prestador se identifica por `request.responsavel`,
                # então precisa aparecer em toda tela do portal.
                self.assertContains(resposta, 'Portal do Prestador')
                self.assertContains(resposta, self.responsavel.nome)

    def test_tela_de_cadastrar_colaborador(self):
        self.empresa.status = 'aguardando_assinatura'
        self.empresa.save()
        self.client.force_login(self.responsavel.user)

        resposta = self.client.get(reverse('terceiros:cadastrar_colaborador'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, self.responsavel.nome)

    def test_sidebar_do_rh_tem_o_item_terceiros(self):
        self.client.force_login(self.rh)

        resposta = self.client.get(reverse('terceiros:listar_empresas'))

        self.assertContains(resposta, reverse('terceiros:listar_empresas'))
        self.assertContains(resposta, 'Terceiros')


class ClausulasIniciaisTests(TestCase):
    def test_biblioteca_inicial_foi_populada_pela_migration(self):
        clausulas = ClausulaContratual.objects.filter(ativa=True)

        self.assertGreater(clausulas.count(), 20)
        self.assertTrue(clausulas.filter(titulo='Do Foro').exists())
        # A numeração é aplicada na renderização, então os textos não podem
        # trazer "Cláusula Xª" escrito.
        for clausula in clausulas:
            self.assertNotIn('Cláusula Primeira', clausula.texto)


class PadraoDeRecorrenciaTests(TestCase):
    """Os três padrões da seção 1 do adendo: único, múltiplo livre e mensal."""

    def setUp(self):
        self.empresa = _criar_empresa(status='aguardando_assinatura')
        self.responsavel = _criar_responsavel(self.empresa)
        self.colaborador = ColaboradorTerceiro.objects.create(
            empresa=self.empresa, nome='João Silva', cpf='39053344705',
        )

    def _enviar(self, tipo, nome='doc.pdf', mes=None, ano=None):
        return DocumentoColaborador.registrar_envio(
            colaborador=self.colaborador, tipo=tipo, arquivo=_arquivo(nome),
            enviado_por=self.responsavel.user, competencia_mes=mes, competencia_ano=ano,
        )

    def test_tipo_unico_substitui_em_vez_de_empilhar(self):
        primeiro = self._enviar('aso', 'primeiro.pdf')
        segundo = self._enviar('aso', 'segundo.pdf')

        self.assertEqual(primeiro.pk, segundo.pk)
        self.assertEqual(self.colaborador.documentos.filter(tipo='aso').count(), 1)
        self.assertIn('segundo', segundo.arquivo.name)

    def test_ordem_de_servico_cria_registro_novo_a_cada_envio(self):
        self._enviar('ordem_servico', 'os1.pdf')
        self._enviar('ordem_servico', 'os2.pdf')
        self._enviar('ordem_servico', 'os3.pdf')

        self.assertEqual(self.colaborador.ordens_servico().count(), 3)

    def test_holerite_substitui_na_mesma_competencia_e_cria_em_competencia_nova(self):
        setembro = self._enviar('holerite', 'set.pdf', mes=9, ano=2026)
        mesmo_mes = self._enviar('holerite', 'set-corrigido.pdf', mes=9, ano=2026)
        outubro = self._enviar('holerite', 'out.pdf', mes=10, ano=2026)

        self.assertEqual(setembro.pk, mesmo_mes.pk)
        self.assertNotEqual(setembro.pk, outubro.pk)
        self.assertEqual(self.colaborador.holerites().count(), 2)
        self.assertIn('set-corrigido', mesmo_mes.arquivo.name)

    def test_reenvio_de_mensal_rejeitado_volta_para_analise(self):
        documento = self._enviar('holerite', 'set.pdf', mes=9, ano=2026)
        documento.status = 'rejeitado'
        documento.motivo_rejeicao = 'Valor divergente.'
        documento.save()

        self._enviar('holerite', 'set-v2.pdf', mes=9, ano=2026)

        documento.refresh_from_db()
        self.assertEqual(documento.status, 'pendente')
        self.assertEqual(documento.motivo_rejeicao, '')

    def test_competencia_aparece_com_nome_do_mes(self):
        documento = self._enviar('holerite', 'set.pdf', mes=9, ano=2026)
        self.assertEqual(documento.competencia_display, 'Setembro/2026')

    def test_tipo_sem_competencia_nao_mostra_rotulo(self):
        self.assertEqual(self._enviar('aso').competencia_display, '')


class CriterioDeValidacaoTests(TestCase):
    """Seção 5: o que entra e o que não entra no critério de cadastro completo."""

    def setUp(self):
        self.empresa = _criar_empresa(status='aguardando_assinatura')
        self.responsavel = _criar_responsavel(self.empresa)
        self.colaborador = ColaboradorTerceiro.objects.create(
            empresa=self.empresa, nome='João Silva', cpf='39053344705',
        )

    def _completar_onboarding(self, status='aprovado'):
        for tipo in DocumentoColaborador.TIPOS_OBRIGATORIOS_ONBOARDING:
            DocumentoColaborador.objects.create(
                colaborador=self.colaborador, tipo=tipo, arquivo=_arquivo(), status=status,
            )

    def test_holerite_nao_conta_como_pendencia_de_cadastro(self):
        self._completar_onboarding()
        pendentes = dict(self.colaborador.tipos_pendentes())

        self.assertNotIn('holerite', pendentes)
        self.assertTrue(self.colaborador.documentacao_completa())

    def test_falta_de_ordem_de_servico_deixa_o_cadastro_incompleto(self):
        for tipo in DocumentoColaborador.TIPOS_UNICOS:
            DocumentoColaborador.objects.create(
                colaborador=self.colaborador, tipo=tipo, arquivo=_arquivo(), status='aprovado',
            )

        self.assertIn('ordem_servico', dict(self.colaborador.tipos_pendentes()))
        self.assertFalse(self.colaborador.documentos_todos_aprovados())

    def test_uma_unica_ordem_de_servico_aprovada_basta(self):
        self._completar_onboarding()
        DocumentoColaborador.objects.create(
            colaborador=self.colaborador, tipo='ordem_servico',
            arquivo=_arquivo('extra.pdf'), status='pendente',
        )

        # A segunda OS ainda em análise não derruba o critério: basta existir uma aprovada.
        self.assertTrue(self.colaborador.documentos_todos_aprovados())

    def test_holerite_pendente_nao_impede_validacao_da_empresa(self):
        self._completar_onboarding()
        DocumentoColaborador.objects.create(
            colaborador=self.colaborador, tipo='holerite', arquivo=_arquivo(),
            competencia_mes=9, competencia_ano=2026, status='pendente',
        )

        self.assertTrue(self.empresa.documentos_todos_aprovados())


class DocumentoEmpresaTerceiraTests(TestCase):
    def setUp(self):
        self.empresa = _criar_empresa(status='ativo')
        self.responsavel = _criar_responsavel(self.empresa)
        self.rh = _criar_usuario_rh()

    def _enviar(self, tipo='fgts', nome='guia.pdf', mes=9, ano=2026):
        return DocumentoEmpresaTerceira.registrar_envio(
            empresa=self.empresa, tipo=tipo, arquivo=_arquivo(nome),
            enviado_por=self.responsavel.user, competencia_mes=mes, competencia_ano=ano,
        )

    def test_mesma_competencia_substitui_competencia_nova_cria(self):
        setembro = self._enviar(mes=9)
        mesmo_mes = self._enviar(nome='guia-v2.pdf', mes=9)
        outubro = self._enviar(mes=10)

        self.assertEqual(setembro.pk, mesmo_mes.pk)
        self.assertNotEqual(setembro.pk, outubro.pk)
        self.assertEqual(self.empresa.documentos_mensais.count(), 2)

    def test_fgts_e_dctfweb_do_mesmo_mes_convivem(self):
        self._enviar(tipo='fgts', mes=9)
        self._enviar(tipo='dctfweb', mes=9)

        self.assertEqual(self.empresa.documentos_mensais.count(), 2)

    def test_responsavel_envia_pelo_formulario(self):
        self.client.force_login(self.responsavel.user)

        self.client.post(reverse('terceiros:documentacao_mensal'), {
            'tipo': 'dctfweb', 'competencia_mes': '9', 'competencia_ano': '2026',
            'arquivo': _arquivo('dctf.pdf'),
        })

        documento = self.empresa.documentos_mensais.get()
        self.assertEqual(documento.tipo, 'dctfweb')
        self.assertEqual(documento.competencia_display, 'Setembro/2026')

    def test_envio_sem_competencia_e_recusado(self):
        self.client.force_login(self.responsavel.user)

        resposta = self.client.post(reverse('terceiros:documentacao_mensal'), {
            'tipo': 'fgts', 'arquivo': _arquivo('guia.pdf'),
        })

        self.assertEqual(self.empresa.documentos_mensais.count(), 0)
        self.assertContains(resposta, 'Informe o mês de competência.')

    def test_rh_aprova_documento_de_empresa_ja_ativa(self):
        documento = self._enviar()
        self.client.force_login(self.rh)

        self.client.post(reverse('terceiros:aprovar_documento_empresa', args=[documento.id]))

        documento.refresh_from_db()
        self.assertEqual(documento.status, 'aprovado')
        self.assertEqual(documento.analisado_por, self.rh)

    def test_rejeitar_mensal_nao_devolve_a_empresa_para_reenvio(self):
        documento = self._enviar()
        self.client.force_login(self.rh)

        self.client.post(
            reverse('terceiros:rejeitar_documento_empresa', args=[documento.id]),
            {'motivo': 'Guia ilegível.'},
        )

        documento.refresh_from_db()
        self.empresa.refresh_from_db()
        self.assertEqual(documento.status, 'rejeitado')
        self.assertEqual(self.empresa.status, 'ativo')

    def test_responsavel_de_outra_empresa_nao_baixa_o_arquivo(self):
        documento = self._enviar()
        outra = _criar_empresa(cnpj='99888777000166', status='ativo')
        outro_responsavel = _criar_responsavel(outra, cpf='39053344705')
        self.client.force_login(outro_responsavel.user)

        resposta = self.client.get(
            reverse('terceiros:ver_documento_empresa', args=[documento.id])
        )

        self.assertEqual(resposta.status_code, 302)

    def test_competencias_agrupam_documentos_da_empresa_e_holerites(self):
        colaborador = ColaboradorTerceiro.objects.create(
            empresa=self.empresa, nome='João Silva', cpf='39053344705',
        )
        self._enviar(tipo='fgts', mes=9)
        DocumentoColaborador.objects.create(
            colaborador=colaborador, tipo='holerite', arquivo=_arquivo(),
            competencia_mes=9, competencia_ano=2026,
        )
        self._enviar(tipo='fgts', mes=10)

        competencias = self.empresa.competencias_mensais()

        self.assertEqual([c['rotulo'] for c in competencias], ['Outubro/2026', 'Setembro/2026'])
        self.assertEqual(len(competencias[1]['documentos_empresa']), 1)
        self.assertEqual(len(competencias[1]['holerites']), 1)
        self.assertEqual(len(competencias[0]['holerites']), 0)


class FormularioDeEnvioTests(TestCase):
    def setUp(self):
        self.empresa = _criar_empresa(status='aguardando_assinatura')
        self.responsavel = _criar_responsavel(self.empresa)
        self.colaborador = ColaboradorTerceiro.objects.create(
            empresa=self.empresa, nome='João Silva', cpf='39053344705',
        )
        self.client.force_login(self.responsavel.user)

    def _postar(self, dados):
        return self.client.post(
            reverse('terceiros:detalhe_colaborador', args=[self.colaborador.id]), dados
        )

    def test_holerite_sem_competencia_e_recusado(self):
        resposta = self._postar({'tipo': 'holerite', 'arquivo': _arquivo()})

        self.assertEqual(self.colaborador.documentos.count(), 0)
        self.assertContains(resposta, 'Informe o mês de competência.')

    def test_competencia_enviada_em_tipo_nao_mensal_e_descartada(self):
        self._postar({
            'tipo': 'aso', 'arquivo': _arquivo(),
            'competencia_mes': '9', 'competencia_ano': '2026',
        })

        documento = self.colaborador.documentos.get()
        self.assertIsNone(documento.competencia_mes)
        self.assertIsNone(documento.competencia_ano)


class RenderizacaoDasTelasNovasTests(TestCase):
    """
    Smoke test das duas telas que exibem o histórico mensal. Sem isso, um erro de
    sintaxe de template só apareceria em produção -- nenhum outro teste as abre.
    """

    def setUp(self):
        self.empresa = _criar_empresa(status='ativo')
        self.responsavel = _criar_responsavel(self.empresa)
        self.rh = _criar_usuario_rh()
        self.colaborador = ColaboradorTerceiro.objects.create(
            empresa=self.empresa, nome='João Silva', cpf='39053344705',
        )
        DocumentoEmpresaTerceira.objects.create(
            empresa=self.empresa, tipo='fgts', arquivo=_arquivo(),
            competencia_mes=9, competencia_ano=2026,
        )
        DocumentoColaborador.objects.create(
            colaborador=self.colaborador, tipo='holerite', arquivo=_arquivo(),
            competencia_mes=9, competencia_ano=2026,
        )
        DocumentoColaborador.objects.create(
            colaborador=self.colaborador, tipo='ordem_servico', arquivo=_arquivo(),
        )

    def test_painel_de_documentos_do_responsavel(self):
        self.client.force_login(self.responsavel.user)

        resposta = self.client.get(reverse('terceiros:documentacao_mensal'))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Setembro/2026')
        self.assertContains(resposta, 'Comprovante de FGTS')
        self.assertContains(resposta, 'João Silva')

    def test_detalhe_da_empresa_no_rh_mostra_as_entregas_mensais(self):
        self.client.force_login(self.rh)

        resposta = self.client.get(reverse('terceiros:detalhe_empresa', args=[self.empresa.id]))

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Entregas mensais')
        self.assertContains(resposta, 'Setembro/2026')

    def test_envio_de_documento_do_colaborador_lista_as_secoes(self):
        self.client.force_login(self.responsavel.user)

        resposta = self.client.get(
            reverse('terceiros:detalhe_colaborador', args=[self.colaborador.id])
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, 'Ordens de Serviço')
        self.assertContains(resposta, 'Holerites')
        self.assertContains(resposta, 'Setembro/2026')


class MinhaEmpresaTests(TestCase):
    """
    Aba "Minha Empresa": o responsável passa a editar os dados da própria
    prestadora, não só os dele.
    """

    def setUp(self):
        self.empresa = _criar_empresa(status='pendente_cadastro')
        self.responsavel = _criar_responsavel(self.empresa)
        self.client.force_login(self.responsavel.user)

    def test_responsavel_corrige_os_dados_da_empresa(self):
        self.client.post(reverse('terceiros:minha_empresa'), _dados_minha_empresa(
            razao_social='Prestadora Alfa ME', logradouro='Rua Nova', cidade='Goiânia',
        ))

        self.empresa.refresh_from_db()
        self.assertEqual(self.empresa.razao_social, 'Prestadora Alfa ME')
        self.assertEqual(self.empresa.logradouro, 'Rua Nova')
        self.assertEqual(self.empresa.cidade, 'Goiânia')

    def test_cnpj_editavel_enquanto_o_cadastro_inicial_nao_fechou(self):
        self.client.post(reverse('terceiros:minha_empresa'), _dados_minha_empresa(
            cnpj='99.888.777/0001-66',
        ))

        self.empresa.refresh_from_db()
        self.assertEqual(self.empresa.cnpj, '99888777000166')

    def test_cnpj_trava_depois_do_cadastro_inicial(self):
        """
        Com o contrato já emitido, mudar o CNPJ deixaria o PDF divergindo do
        cadastro. A trava é do formulário, então nem um POST forjado passa.
        """
        self.empresa.status = 'ativo'
        self.empresa.save()

        self.client.post(reverse('terceiros:minha_empresa'), _dados_minha_empresa(
            cnpj='99.888.777/0001-66', razao_social='Prestadora Alfa ME',
        ))

        self.empresa.refresh_from_db()
        self.assertEqual(self.empresa.cnpj, '11222333000181')
        # O resto do formulário continua valendo -- só o CNPJ é que não.
        self.assertEqual(self.empresa.razao_social, 'Prestadora Alfa ME')

    def test_tela_travada_nao_traz_campo_de_cnpj_editavel(self):
        self.empresa.status = 'ativo'
        self.empresa.save()

        resposta = self.client.get(reverse('terceiros:minha_empresa'))

        self.assertNotContains(resposta, 'name="cnpj"')
        self.assertContains(resposta, 'fale com o RH da Sotrigo')

    def test_dado_invalido_da_empresa_nao_grava_nem_os_dados_do_responsavel(self):
        """Um formulário só: ou os dois lados entram, ou nenhum entra."""
        self.responsavel.profissao = 'Engenheira'
        self.responsavel.save()

        resposta = self.client.post(reverse('terceiros:minha_empresa'), _dados_minha_empresa(
            cep='123', profissao='Administradora',
        ))

        self.assertEqual(resposta.status_code, 200)
        self.responsavel.refresh_from_db()
        self.assertEqual(self.responsavel.profissao, 'Engenheira')


class EscopoDoPortalTests(TestCase):
    """
    Nenhuma aba do portal pode deixar escapar dado de outra prestadora, nem por
    troca de id na URL.
    """

    def setUp(self):
        self.empresa = _criar_empresa(status='ativo')
        self.responsavel = _criar_responsavel(self.empresa)

        self.outra = _criar_empresa(
            status='ativo', cnpj='99888777000166', razao_social='Prestadora Beta LTDA',
        )
        self.colaborador_alheio = ColaboradorTerceiro.objects.create(
            empresa=self.outra, nome='Carlos Beta', cpf='39053344705',
        )
        DocumentoEmpresaTerceira.objects.create(
            empresa=self.outra, tipo='fgts', arquivo=_arquivo(),
            competencia_mes=9, competencia_ano=2026,
        )

        self.client.force_login(self.responsavel.user)

    def test_lista_de_funcionarios_nao_traz_equipe_alheia(self):
        resposta = self.client.get(reverse('terceiros:funcionarios'))

        self.assertNotContains(resposta, 'Carlos Beta')

    def test_detalhe_de_colaborador_alheio_da_404(self):
        resposta = self.client.get(
            reverse('terceiros:detalhe_colaborador', args=[self.colaborador_alheio.id])
        )

        self.assertEqual(resposta.status_code, 404)

    def test_documentacao_mensal_nao_traz_competencia_alheia(self):
        resposta = self.client.get(reverse('terceiros:documentacao_mensal'))

        self.assertNotContains(resposta, 'Setembro/2026')

    def test_minha_empresa_mostra_a_propria_empresa(self):
        resposta = self.client.get(reverse('terceiros:minha_empresa'))

        self.assertContains(resposta, 'Prestadora Alfa LTDA')
        self.assertNotContains(resposta, 'Prestadora Beta LTDA')


class SidebarDoPortalTests(TestCase):
    """A sidebar do portal é só dele: nenhum item do sistema principal aparece."""

    def setUp(self):
        self.empresa = _criar_empresa(status='ativo')
        self.responsavel = _criar_responsavel(self.empresa)
        self.client.force_login(self.responsavel.user)

    def test_as_tres_abas_e_o_inicio_aparecem_em_toda_tela(self):
        for nome in ('painel', 'minha_empresa', 'documentacao_mensal', 'funcionarios'):
            with self.subTest(tela=nome):
                resposta = self.client.get(reverse(f'terceiros:{nome}'))

                self.assertContains(resposta, reverse('terceiros:painel'))
                self.assertContains(resposta, reverse('terceiros:minha_empresa'))
                self.assertContains(resposta, reverse('terceiros:documentacao_mensal'))
                self.assertContains(resposta, reverse('terceiros:funcionarios'))

    def test_nenhum_item_do_sistema_principal_vaza_para_o_portal(self):
        resposta = self.client.get(reverse('terceiros:painel'))

        self.assertNotContains(resposta, 'Sotrigo RH')
        self.assertNotContains(resposta, reverse('ponto:meu_ponto'))
        self.assertNotContains(resposta, reverse('solicitar_ferias'))
