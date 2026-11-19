import shutil
import tempfile
from datetime import date, timedelta
from importlib import import_module

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import (
    FichaEPI, Funcionario, ItemCatalogoEPI, ItemEPI, MatrizEpiSetor, OcorrenciaEPI, Setor, SolicitacaoTrocaEPI, Usuario,
)

MEDIA_TESTE = tempfile.mkdtemp()


def _criar_usuario(nome, cpf, papel, setor, cargo='Analista', acesso_rh=False):
    funcionario = Funcionario.objects.create(nome=nome, cpf=cpf, cargo=cargo, setor=setor, status='ativo')
    user = User.objects.create_user(username=cpf, password='senha123')
    Usuario.objects.create(user=user, funcionario=funcionario, papel=papel, acesso_rh=acesso_rh)
    return user


class DivisaoPerfisTests(TestCase):
    """DP opera os módulos já existentes; RH (papel ou acesso_rh) também vê os exclusivos."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='Administrativo')
        self.user_dp = _criar_usuario('DP Teste', '11111111111', 'dp', self.setor)
        self.user_rh = _criar_usuario('RH Teste', '22222222222', 'rh', self.setor)
        self.user_seguranca = _criar_usuario('Tecnico SST', '33333333333', 'seguranca_trabalho', self.setor)
        self.colaborador = Funcionario.objects.create(
            nome='Colaborador', cpf='44444444444', cargo='Operador', setor=self.setor, status='ativo',
        )
        User.objects.create_user(username='44444444444', password='senha123')
        Usuario.objects.create(
            user=User.objects.get(username='44444444444'), funcionario=self.colaborador, papel='colaborador',
        )

    def test_dp_acessa_os_modulos_existentes(self):
        self.client.force_login(self.user_dp)
        for nome in ('listar_funcionarios', 'listar_comodatos', 'concluir_ferias', 'rh_vencimento_ferias',
                     'ponto:listar_equipamentos', 'terceiros:listar_empresas'):
            with self.subTest(nome=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 200)

    def test_dp_edita_dados_do_funcionario(self):
        # Checagem interna da view (não só o decorator) também precisa aceitar o DP.
        self.client.force_login(self.user_dp)
        response = self.client.get(reverse('detalhe_funcionario', args=[self.colaborador.id]))
        self.assertContains(response, reverse('editar_dados_funcionario', args=[self.colaborador.id]))

    def _salvar_cargo(self, cargo):
        return self.client.post(reverse('editar_dados_funcionario', args=[self.colaborador.id]), {
            'matricula': '', 'codigo_empresa': 453, 'cargo': cargo, 'setor': self.setor.id,
            'data_admissao': '', 'salario': '',
        })

    def test_rh_altera_cargo_com_sugestoes_dos_cargos_existentes(self):
        self.client.force_login(self.user_rh)
        response = self.client.get(reverse('detalhe_funcionario', args=[self.colaborador.id]))
        self.assertContains(response, 'list="cargos-existentes"')
        self.assertContains(response, '<option value="Analista">')

        self._salvar_cargo('Supervisor de Produção')
        self.colaborador.refresh_from_db()
        self.assertEqual(self.colaborador.cargo, 'Supervisor de Produção')

    def test_cargo_digitado_reaproveita_a_grafia_existente(self):
        self.client.force_login(self.user_rh)
        self._salvar_cargo('  analista ')
        self.colaborador.refresh_from_db()
        self.assertEqual(self.colaborador.cargo, 'Analista')

    def test_colaborador_nao_altera_cargo(self):
        self.client.force_login(User.objects.get(username='44444444444'))
        self._salvar_cargo('Diretor')
        self.colaborador.refresh_from_db()
        self.assertEqual(self.colaborador.cargo, 'Operador')

    def test_dp_nao_concede_acesso_rh(self):
        self.client.force_login(self.user_dp)
        self.client.post(reverse('alternar_acesso_rh', args=[self.colaborador.id]))
        self.assertFalse(Usuario.objects.get(funcionario=self.colaborador).acesso_rh)

    def test_rh_concede_acesso_rh(self):
        self.client.force_login(self.user_rh)
        self.client.post(reverse('alternar_acesso_rh', args=[self.colaborador.id]))
        self.assertTrue(Usuario.objects.get(funcionario=self.colaborador).acesso_rh)

    def test_dp_nao_ve_o_modulo_de_epi(self):
        self.client.force_login(self.user_dp)
        self.assertRedirects(self.client.get(reverse('listar_fichas_epi')), reverse('dashboard'),
                             fetch_redirect_response=False)

    def test_acesso_rh_libera_epi(self):
        user = _criar_usuario('Gestor RH', '55555555555', 'gestor', self.setor, acesso_rh=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('listar_fichas_epi')).status_code, 200)

    def test_seguranca_nao_ve_modulos_de_dp(self):
        # A lista de Funcionários abre (ver PerfilSegurancaTrabalhoTests); o resto do RH/DP, não.
        self.client.force_login(self.user_seguranca)
        telas = [
            ('detalhe_funcionario', [self.colaborador.id]), ('cadastrar_funcionario', []),
            ('listar_comodatos', []), ('concluir_ferias', []), ('rh_vencimento_ferias', []), ('pedidos_senha', []),
            ('ponto:listar_equipamentos', []), ('ponto:ajustes_pendentes', []),
            ('folha:listar', []), ('terceiros:listar_empresas', []),
        ]
        for nome, args in telas:
            with self.subTest(tela=nome):
                self.assertRedirects(self.client.get(reverse(nome, args=args)), reverse('dashboard'),
                                     fetch_redirect_response=False)

    def test_dashboard_da_seguranca_vai_para_as_fichas(self):
        self.client.force_login(self.user_seguranca)
        self.assertRedirects(self.client.get(reverse('dashboard')), reverse('listar_fichas_epi'),
                             fetch_redirect_response=False)

    def test_menu_da_seguranca_mostra_epi_funcionarios_e_meu_espaco(self):
        self.client.force_login(self.user_seguranca)
        response = self.client.get(reverse('listar_fichas_epi'))
        self.assertContains(response, reverse('listar_catalogo_epi'))
        self.assertContains(response, reverse('listar_funcionarios'))
        self.assertContains(response, reverse('meus_documentos'))
        for tela in ('listar_comodatos', 'folha:listar', 'concluir_ferias', 'terceiros:listar_empresas'):
            with self.subTest(tela=tela):
                self.assertNotContains(response, reverse(tela))


class PerfilSegurancaTrabalhoTests(TestCase):
    """
    Perfil de Segurança do Trabalho (pedido de 19/11/2026): aba de Segurança e
    menu de Funcionários, mas a ficha que abre é só a de EPI -- nada das
    funções de RH/DP. Quem dá e tira o perfil é o RH, na ficha do funcionário.
    """

    def setUp(self):
        self.sst = Setor.objects.create(nome='SEGURANCA DO TRABALHO')
        self.producao = Setor.objects.create(nome='PRODUCAO')
        self.user_seguranca = _criar_usuario('Tecnico SST', '33333333333', 'seguranca_trabalho', self.sst)
        self.user_rh = _criar_usuario('RH Teste', '22222222222', 'rh', self.sst)
        self.user_dp = _criar_usuario('DP Teste', '11111111111', 'dp', self.sst)

        self.ana = Funcionario.objects.create(
            nome='Ana Operadora', cpf='44444444444', matricula='9876', cargo='Operadora', setor=self.producao,
            status='ativo', salario='3500.00', data_admissao=date(2024, 3, 4),
        )
        Usuario.objects.create(
            user=User.objects.create_user(username='44444444444'), funcionario=self.ana, papel='colaborador',
        )

        self.protetor = ItemCatalogoEPI.objects.create(nome='Protetor auricular', numero_ca='11223')
        self.luva = ItemCatalogoEPI.objects.create(nome='Luva nitrílica', numero_ca='44556')
        MatrizEpiSetor.objects.create(setor=self.producao, item_catalogo=self.protetor)
        MatrizEpiSetor.objects.create(setor=self.producao, item_catalogo=self.luva)
        ficha = FichaEPI.objects.create(funcionario=self.ana, status='ativo', data_entrega=date(2026, 1, 10))
        ItemEPI.objects.create(ficha=ficha, item_catalogo=self.protetor, data_vencimento=date(2026, 1, 20))
        OcorrenciaEPI.objects.create(
            funcionario=self.ana, data_ocorrencia=date(2026, 11, 3), descricao='Sem protetor na linha 2.',
            item_catalogo=self.protetor,
        )

    def test_lista_mostra_todos_e_abre_so_a_ficha_de_epi(self):
        self.client.force_login(self.user_seguranca)

        response = self.client.get(reverse('listar_funcionarios'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ana Operadora')  # de outro setor: a Segurança vê todos
        self.assertContains(response, reverse('funcionario_epi', args=[self.ana.id]))
        # Entre aspas: "/funcionarios/<id>/" também é o final de "/epi/funcionarios/<id>/".
        self.assertNotContains(response, f'"{reverse("detalhe_funcionario", args=[self.ana.id])}"')
        self.assertNotContains(response, reverse('cadastrar_funcionario'))

    def test_busca_por_matricula_ou_setor_mas_nao_por_cpf(self):
        self.client.force_login(self.user_seguranca)
        url = reverse('listar_funcionarios')

        self.assertContains(self.client.get(url, {'busca': '9876'}), 'Ana Operadora')
        self.assertContains(self.client.get(url, {'busca': 'produc'}), 'Ana Operadora')
        self.assertNotContains(self.client.get(url, {'busca': '44444444444'}), 'Ana Operadora')

    def test_ficha_de_epi_mostra_epi_e_esconde_dados_do_rh(self):
        self.client.force_login(self.user_seguranca)

        response = self.client.get(reverse('funcionario_epi', args=[self.ana.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Protetor auricular')
        self.assertContains(response, 'Troca vencida')
        self.assertContains(response, 'Luva nitrílica')  # exigida pela matriz e ainda não entregue
        self.assertContains(response, '1 sem entrega')
        self.assertContains(response, 'Sem protetor na linha 2.')
        self.assertContains(response, f"{reverse('criar_ficha_epi')}?funcionario={self.ana.id}")
        self.assertNotContains(response, '44444444444')  # CPF
        self.assertNotContains(response, '3.500')  # salário
        self.assertNotContains(response, reverse('editar_dados_funcionario', args=[self.ana.id]))

    def test_ficha_de_epi_fechada_para_dp_gestor_e_colaborador(self):
        gestor = _criar_usuario('Gestor Prod', '55555555555', 'gestor', self.producao)
        for user in (self.user_dp, gestor, User.objects.get(username='44444444444')):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse('funcionario_epi', args=[self.ana.id]))
                self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)

    def test_nao_altera_dados_nem_senha(self):
        self.client.force_login(self.user_seguranca)

        self.client.post(reverse('editar_dados_funcionario', args=[self.ana.id]), {
            'matricula': '9876', 'codigo_empresa': 453, 'cargo': 'Diretora', 'setor': self.producao.id,
            'data_admissao': '', 'salario': '9999',
        })
        response = self.client.post(reverse('resetar_senha_funcionario', args=[self.ana.id]))

        self.assertRedirects(response, reverse('dashboard'), fetch_redirect_response=False)
        self.ana.refresh_from_db()
        self.assertEqual(self.ana.cargo, 'Operadora')
        self.assertFalse(Usuario.objects.get(funcionario=self.ana).senha_provisoria)

    def test_seguranca_com_acesso_rh_continua_com_a_ficha_completa(self):
        user = _criar_usuario('Tecnico com RH', '66666666666', 'seguranca_trabalho', self.sst, acesso_rh=True)
        self.client.force_login(user)

        response = self.client.get(reverse('listar_funcionarios'))

        self.assertContains(response, f'"{reverse("detalhe_funcionario", args=[self.ana.id])}"')

    def test_nova_ficha_e_ocorrencia_ja_vem_com_o_funcionario(self):
        self.client.force_login(self.user_seguranca)
        for nome in ('criar_ficha_epi', 'criar_ocorrencia_epi'):
            with self.subTest(tela=nome):
                response = self.client.get(reverse(nome), {'funcionario': self.ana.id})
                self.assertContains(response, f'value="{self.ana.id}" selected')

    def test_rh_da_e_tira_o_perfil(self):
        self.client.force_login(self.user_rh)
        url = reverse('alternar_seguranca_trabalho', args=[self.ana.id])

        self.client.post(url)
        self.assertEqual(Usuario.objects.get(funcionario=self.ana).papel, 'seguranca_trabalho')
        self.client.post(url)
        self.assertEqual(Usuario.objects.get(funcionario=self.ana).papel, 'colaborador')

    def test_detalhe_do_rh_mostra_o_botao(self):
        self.client.force_login(self.user_rh)

        response = self.client.get(reverse('detalhe_funcionario', args=[self.ana.id]))

        self.assertContains(response, reverse('alternar_seguranca_trabalho', args=[self.ana.id]))
        self.assertContains(response, 'Dar perfil de Segurança do Trabalho')

    def test_dp_nao_da_o_perfil(self):
        self.client.force_login(self.user_dp)

        self.client.post(reverse('alternar_seguranca_trabalho', args=[self.ana.id]))

        self.assertEqual(Usuario.objects.get(funcionario=self.ana).papel, 'colaborador')

    def test_nao_troca_papel_de_gestor_dp_ou_rh(self):
        self.client.force_login(self.user_rh)
        gestor = _criar_usuario('Gestor Prod', '55555555555', 'gestor', self.producao)
        for user in (gestor, self.user_dp):
            with self.subTest(papel=user.usuario.papel):
                papel_antes = user.usuario.papel
                self.client.post(reverse('alternar_seguranca_trabalho', args=[user.usuario.funcionario_id]))
                self.assertEqual(Usuario.objects.get(user=user).papel, papel_antes)


class MigracaoRhViraDpTests(TestCase):
    def test_rh_vira_dp_e_volta(self):
        setor = Setor.objects.create(nome='RH')
        _criar_usuario('Antigo RH', '66666666666', 'rh', setor)
        migracao = import_module('funcionarios.migrations.0032_rh_vira_dp')

        migracao.rh_vira_dp(django_apps, None)
        self.assertEqual(Usuario.objects.get().papel, 'dp')

        migracao.dp_volta_rh(django_apps, None)
        self.assertEqual(Usuario.objects.get().papel, 'rh')


@override_settings(MEDIA_ROOT=MEDIA_TESTE)
class FichaEPIFluxoTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_TESTE, ignore_errors=True)

    def setUp(self):
        self.setor = Setor.objects.create(nome='Produção')
        self.user_seguranca = _criar_usuario('Tecnico SST', '33333333333', 'seguranca_trabalho', self.setor)
        self.user_colaborador = _criar_usuario('Operador', '44444444444', 'colaborador', self.setor, cargo='Operador')
        self.funcionario = Funcionario.objects.get(cpf='44444444444')
        self.botina = ItemCatalogoEPI.objects.create(nome='Botina', numero_ca='12345')
        self.luva = ItemCatalogoEPI.objects.create(nome='Luva', numero_ca='67890')

    def _post_ficha(self, itens):
        dados = {
            'funcionario': self.funcionario.id,
            'itens-TOTAL_FORMS': str(len(itens)), 'itens-INITIAL_FORMS': '0',
            'itens-MIN_NUM_FORMS': '0', 'itens-MAX_NUM_FORMS': '1000',
        }
        for i, (item, quantidade, vencimento) in enumerate(itens):
            dados[f'itens-{i}-item_catalogo'] = item.id if item else ''
            dados[f'itens-{i}-quantidade'] = quantidade
            dados[f'itens-{i}-data_vencimento'] = vencimento
        return self.client.post(reverse('criar_ficha_epi'), dados)

    def test_cria_ficha_com_itens(self):
        self.client.force_login(self.user_seguranca)
        response = self._post_ficha([(self.botina, 1, '2027-04-01'), (self.luva, 2, '')])

        self.assertRedirects(response, reverse('listar_fichas_epi'), fetch_redirect_response=False)
        ficha = FichaEPI.objects.get()
        self.assertEqual(ficha.gerado_por, self.user_seguranca)
        self.assertEqual(ficha.itens.count(), 2)
        self.assertEqual(ficha.itens.get(item_catalogo=self.botina).data_vencimento, date(2027, 4, 1))
        # A entrega só é registrada quando a ficha assinada for validada.
        self.assertIsNone(ficha.data_entrega)

    def test_ficha_criada_ja_aparece_para_o_colaborador_assinar(self):
        self.client.force_login(self.user_seguranca)
        self._post_ficha([(self.botina, 1, '')])
        ficha = FichaEPI.objects.get()
        self.assertTrue(ficha.arquivo_gerado)
        self.assertEqual(ficha.status, 'aguardando_assinatura')

        self.client.force_login(self.user_colaborador)
        meus = self.client.get(reverse('meus_documentos'))
        self.assertContains(meus, reverse('reenviar_epi_assinado', args=[ficha.id]))

    def test_pesquisa_ficha_por_funcionario_ou_setor(self):
        outro_setor = Setor.objects.create(nome='Expedição')
        outro = Funcionario.objects.create(nome='Bruno Silva', cpf='55555555555', cargo='Op', setor=outro_setor, status='ativo')
        FichaEPI.objects.create(funcionario=self.funcionario)
        FichaEPI.objects.create(funcionario=outro)
        self.client.force_login(self.user_seguranca)
        url = reverse('listar_fichas_epi')

        def nomes(busca):
            return [f.funcionario.nome for f in self.client.get(url, {'busca': busca}).context['fichas']]

        self.assertEqual(nomes('bruno'), ['Bruno Silva'])
        self.assertEqual(nomes('produção'), ['Operador'])
        self.assertEqual(len(nomes('')), 2)
        self.assertContains(self.client.get(url, {'busca': 'ninguem'}), 'Nenhuma ficha encontrada')

    def test_ficha_sem_itens_e_recusada(self):
        self.client.force_login(self.user_seguranca)
        response = self._post_ficha([(None, 1, '')])
        self.assertEqual(response.status_code, 200)
        self.assertFalse(FichaEPI.objects.exists())

    def test_matriz_do_setor_vai_para_a_tela_de_criacao(self):
        MatrizEpiSetor.objects.create(setor=self.setor, item_catalogo=self.botina, quantidade_padrao=2)
        self.client.force_login(self.user_seguranca)
        response = self.client.get(reverse('criar_ficha_epi'))
        self.assertEqual(response.context['matriz_por_setor'], {str(self.setor.id): [{'item': str(self.botina.id), 'quantidade': 2}]})
        busca = {f['id']: f for f in response.context['funcionarios_busca']}
        self.assertEqual(busca[self.funcionario.id]['setor_id'], self.setor.id)

    def test_pdf_traz_o_ca(self):
        ficha = FichaEPI.objects.create(funcionario=self.funcionario, data_entrega=date(2026, 10, 1))
        ItemEPI.objects.create(ficha=ficha, item_catalogo=self.botina)
        self.client.force_login(self.user_seguranca)

        self.client.post(reverse('gerar_pdf_ficha_epi', args=[ficha.id]))

        ficha.refresh_from_db()
        self.assertTrue(ficha.arquivo_gerado)
        self.assertEqual(ficha.status, 'aguardando_assinatura')

    def test_colaborador_baixa_e_reenvia_assinado_e_seguranca_valida(self):
        ficha = FichaEPI.objects.create(funcionario=self.funcionario)
        ficha.arquivo_gerado.save('ficha.pdf', ContentFile(b'%PDF-1.4'))

        self.client.force_login(self.user_colaborador)
        meus = self.client.get(reverse('meus_documentos'))
        self.assertContains(meus, reverse('reenviar_epi_assinado', args=[ficha.id]))
        self.assertEqual(self.client.get(reverse('ver_ficha_epi', args=[ficha.id, 'gerado'])).status_code, 200)

        self.client.post(
            reverse('reenviar_epi_assinado', args=[ficha.id]),
            {'arquivo_assinado': SimpleUploadedFile('assinado.pdf', b'%PDF-1.4', content_type='application/pdf')},
        )
        ficha.refresh_from_db()
        self.assertEqual(ficha.status, 'em_validacao')

        self.client.force_login(self.user_seguranca)
        self.client.post(reverse('aprovar_ficha_epi', args=[ficha.id]))
        ficha.refresh_from_db()
        self.assertEqual(ficha.status, 'ativo')
        self.assertEqual(ficha.validado_por, self.user_seguranca)
        self.assertEqual(ficha.data_entrega, timezone.localdate())

    def test_rejeicao_volta_para_assinatura_com_motivo(self):
        ficha = FichaEPI.objects.create(
            funcionario=self.funcionario, data_entrega=date(2026, 10, 1), status='em_validacao',
        )
        self.client.force_login(self.user_seguranca)
        self.client.post(reverse('rejeitar_ficha_epi', args=[ficha.id]), {'motivo': 'Sem assinatura'})
        ficha.refresh_from_db()
        self.assertEqual(ficha.status, 'aguardando_assinatura')
        self.assertEqual(ficha.motivo_rejeicao, 'Sem assinatura')

    def test_colaborador_nao_reenvia_ficha_de_outro(self):
        outro = Funcionario.objects.create(nome='Outro', cpf='77777777777', cargo='X', setor=self.setor)
        ficha = FichaEPI.objects.create(funcionario=outro, data_entrega=date(2026, 10, 1))
        ficha.arquivo_gerado.save('ficha.pdf', ContentFile(b'%PDF-1.4'))
        self.client.force_login(self.user_colaborador)

        response = self.client.post(
            reverse('reenviar_epi_assinado', args=[ficha.id]),
            {'arquivo_assinado': SimpleUploadedFile('a.pdf', b'x', content_type='application/pdf')},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.client.get(reverse('ver_ficha_epi', args=[ficha.id, 'gerado'])).status_code, 302)

    def test_epis_vencendo_lista_vencidos_e_proximos(self):
        hoje = timezone.now().date()
        ficha = FichaEPI.objects.create(funcionario=self.funcionario, data_entrega=hoje)
        vencido = ItemEPI.objects.create(ficha=ficha, item_catalogo=self.botina, data_vencimento=hoje - timedelta(days=3))
        proximo = ItemEPI.objects.create(ficha=ficha, item_catalogo=self.luva, data_vencimento=hoje + timedelta(days=10))
        ItemEPI.objects.create(ficha=ficha, item_catalogo=self.luva, data_vencimento=hoje + timedelta(days=90))
        ItemEPI.objects.create(ficha=ficha, item_catalogo=self.luva, data_vencimento=hoje, devolvido=True)
        self.client.force_login(self.user_seguranca)

        response = self.client.get(reverse('listar_epis_vencendo'))

        self.assertEqual(list(response.context['itens']), [vencido, proximo])
        self.assertContains(response, 'Vencido há 3 dias')

    def test_fichas_e_vencendo_sao_abas_da_mesma_tela(self):
        hoje = timezone.now().date()
        ficha = FichaEPI.objects.create(funcionario=self.funcionario, data_entrega=hoje)
        ItemEPI.objects.create(ficha=ficha, item_catalogo=self.botina, data_vencimento=hoje - timedelta(days=1))
        self.client.force_login(self.user_seguranca)

        for url in (reverse('listar_fichas_epi'), reverse('listar_epis_vencendo')):
            response = self.client.get(url)
            self.assertContains(response, 'class="tabs"')
            self.assertContains(response, f'href="{reverse("listar_epis_vencendo")}"')
            self.assertContains(response, f'href="{reverse("listar_fichas_epi")}"')
            self.assertEqual(response.context['total_pendencias_epi'], 1)
            # O alerta de troca virou aba: não tem mais item próprio na sidebar.
            self.assertNotContains(response, 'EPIs Vencendo')

    def _desligar_com_epi(self):
        hoje = timezone.now().date()
        ficha = FichaEPI.objects.create(funcionario=self.funcionario, data_entrega=hoje, status='ativo')
        botina = ItemEPI.objects.create(ficha=ficha, item_catalogo=self.botina, data_vencimento=hoje - timedelta(days=1))
        luva = ItemEPI.objects.create(ficha=ficha, item_catalogo=self.luva)
        self.funcionario.status = 'inativo'
        self.funcionario.save()
        return ficha, botina, luva

    def test_desligado_com_epi_notifica_devolucao_e_sai_da_troca(self):
        ficha, botina, _ = self._desligar_com_epi()
        self.client.force_login(self.user_seguranca)

        response = self.client.get(reverse('listar_epis_vencendo'))

        self.assertEqual(response.context['devolucoes'], [ficha])
        self.assertEqual(response.context['itens'], [])  # desligado devolve, não troca
        self.assertContains(response, reverse('registrar_devolucao_epi', args=[ficha.id]))
        self.assertEqual(response.context['badge_devolucoes_epi'], 1)
        # Sem ficha para validar, o atalho da sidebar leva direto às devoluções.
        self.assertContains(response, f'href="{reverse("listar_epis_vencendo")}">')

    def test_devolucao_parcial_e_depois_total(self):
        ficha, botina, luva = self._desligar_com_epi()
        self.client.force_login(self.user_seguranca)
        url = reverse('registrar_devolucao_epi', args=[ficha.id])

        self.client.post(url, {'itens': [botina.id], 'observacoes': 'Botina gasta'})
        ficha.refresh_from_db()
        self.assertEqual(ficha.status, 'devolvido_parcial')
        self.assertEqual(ficha.observacoes_devolucao, 'Botina gasta')
        self.assertEqual(list(FichaEPI.com_devolucao_pendente()), [ficha])

        self.client.post(url, {'itens': [luva.id]})
        ficha.refresh_from_db()
        self.assertEqual(ficha.status, 'devolvido_total')
        self.assertEqual(ficha.data_devolucao, timezone.now().date())
        self.assertFalse(FichaEPI.com_devolucao_pendente().exists())

    def test_devolucao_sem_item_marcado_nao_altera_a_ficha(self):
        ficha, _, _ = self._desligar_com_epi()
        self.client.force_login(self.user_seguranca)
        self.client.post(reverse('registrar_devolucao_epi', args=[ficha.id]), {})
        ficha.refresh_from_db()
        self.assertEqual(ficha.status, 'ativo')

    def test_colaborador_nao_registra_devolucao(self):
        ficha, botina, _ = self._desligar_com_epi()
        self.client.force_login(self.user_colaborador)
        self.client.post(reverse('registrar_devolucao_epi', args=[ficha.id]), {'itens': [botina.id]})
        botina.refresh_from_db()
        self.assertFalse(botina.devolvido)

    def test_matriz_salva_e_remove_itens_do_setor(self):
        self.client.force_login(self.user_seguranca)
        url = reverse('gerenciar_matriz_epi')

        response = self.client.post(url, {'setor': self.setor.id, f'item_{self.botina.id}': '1', f'quantidade_{self.botina.id}': '2'})
        self.assertRedirects(response, url)  # salvar fecha a edição e volta às caixinhas
        self.assertEqual(list(MatrizEpiSetor.objects.values_list('item_catalogo', 'quantidade_padrao')), [(self.botina.id, 2)])

        self.client.post(url, {'setor': self.setor.id, f'item_{self.luva.id}': '1'})
        self.assertEqual(list(MatrizEpiSetor.objects.values_list('item_catalogo', flat=True)), [self.luva.id])

        response = self.client.get(url, {'setor': self.setor.id})
        self.assertContains(response, 'EPIs obrigatórios — Produção')

    def test_matriz_criada_vira_caixinha_que_abre_a_edicao(self):
        outro = Setor.objects.create(nome='Expedição')
        MatrizEpiSetor.objects.create(setor=self.setor, item_catalogo=self.botina, quantidade_padrao=2)
        self.client.force_login(self.user_seguranca)

        response = self.client.get(reverse('gerenciar_matriz_epi'))
        self.assertEqual([s.nome for s in response.context['matrizes']], ['Produção'])
        self.assertContains(response, f'?setor={self.setor.id}#editar-matriz')
        self.assertContains(response, 'Botina (2x)')
        self.assertNotContains(response, f'?setor={outro.id}#editar-matriz')

        response = self.client.get(reverse('gerenciar_matriz_epi'), {'setor': self.setor.id})
        self.assertContains(response, 'Editando')
        self.assertEqual([l['marcado'] for l in response.context['linhas_catalogo'] if l['item'] == self.botina], [True])

    def test_matriz_recusa_setor_que_nao_existe(self):
        self.client.force_login(self.user_seguranca)
        for setor in ('99999', 'abc'):
            self.client.post(reverse('gerenciar_matriz_epi'), {'setor': setor, f'item_{self.botina.id}': '1'})
        self.assertFalse(MatrizEpiSetor.objects.exists())

    def test_catalogo_lista_e_matriz_ficam_na_mesma_aba(self):
        self.client.force_login(self.user_seguranca)
        response = self.client.get(reverse('listar_catalogo_epi'))
        self.assertContains(response, '12345')
        self.assertContains(response, reverse('gerenciar_matriz_epi'))
        response = self.client.get(reverse('gerenciar_matriz_epi'))
        self.assertContains(response, reverse('listar_catalogo_epi'))

    def test_cadastra_epi_sem_ca_e_edita_ca(self):
        self.client.force_login(self.user_seguranca)
        self.client.post(reverse('cadastrar_epi_catalogo'), {
            'nome': ' Uniforme camisa ', 'numero_ca': '', 'durabilidade': '6 a 12 meses',
            'criterio_substituicao': 'Avaliar desgaste.', 'ativo': 'on',
        })
        uniforme = ItemCatalogoEPI.objects.get(nome='Uniforme camisa')
        self.assertEqual((uniforme.numero_ca, uniforme.durabilidade, str(uniforme)), ('', '6 a 12 meses', 'Uniforme camisa'))

        self.client.post(reverse('editar_epi_catalogo', args=[self.botina.id]), {
            'nome': 'Botina', 'numero_ca': '48413', 'validade_ca': '2027-11-15', 'ativo': 'on',
        })
        self.botina.refresh_from_db()
        self.assertEqual((self.botina.numero_ca, self.botina.validade_ca), ('48413', date(2027, 11, 15)))

    def test_catalogo_recusa_nome_repetido_e_validade_sem_ca(self):
        self.client.force_login(self.user_seguranca)
        response = self.client.post(reverse('cadastrar_epi_catalogo'), {'nome': 'BOTINA', 'numero_ca': '1'})
        self.assertContains(response, 'Já existe um EPI com esse nome')
        response = self.client.post(reverse('cadastrar_epi_catalogo'), {'nome': 'Carneira', 'validade_ca': '2027-01-01'})
        self.assertContains(response, 'Informe o número do CA')
        self.assertEqual(ItemCatalogoEPI.objects.count(), 2)

    def test_inativar_tira_da_ficha_e_da_matriz(self):
        self.client.force_login(self.user_seguranca)
        self.client.post(reverse('editar_epi_catalogo', args=[self.luva.id]), {'nome': 'Luva', 'numero_ca': '67890'})
        self.luva.refresh_from_db()
        self.assertFalse(self.luva.ativo)
        response = self.client.get(reverse('criar_ficha_epi'))
        self.assertNotIn(self.luva, response.context['formset'].forms[0].fields['item_catalogo'].queryset)

    def test_exclui_so_epi_que_nunca_foi_usado(self):
        MatrizEpiSetor.objects.create(setor=self.setor, item_catalogo=self.botina)
        self.client.force_login(self.user_seguranca)
        self.client.post(reverse('excluir_epi_catalogo', args=[self.botina.id]))
        self.client.post(reverse('excluir_epi_catalogo', args=[self.luva.id]))
        self.assertEqual(list(ItemCatalogoEPI.objects.values_list('nome', flat=True)), ['Botina'])

    def test_colaborador_nao_edita_catalogo(self):
        self.client.force_login(self.user_colaborador)
        self.client.post(reverse('editar_epi_catalogo', args=[self.botina.id]), {'nome': 'X', 'numero_ca': '1', 'ativo': 'on'})
        self.client.post(reverse('excluir_epi_catalogo', args=[self.luva.id]))
        self.assertEqual(ItemCatalogoEPI.objects.count(), 2)
        self.botina.refresh_from_db()
        self.assertEqual(self.botina.nome, 'Botina')

    def test_aviso_de_troca_fica_so_dentro_de_editar(self):
        self.client.force_login(self.user_seguranca)
        self.assertNotContains(self.client.get(reverse('listar_catalogo_epi')), 'Aviso automático sobre troca')
        url = reverse('editar_epi_catalogo', args=[self.botina.id])
        self.assertContains(self.client.get(url), 'Aviso automático sobre troca')

        self.client.post(url, {'nome': 'Botina', 'numero_ca': '12345', 'ativo': 'on'})
        self.botina.refresh_from_db()
        self.assertFalse(self.botina.avisar_troca)
        self.client.post(url, {'nome': 'Botina', 'numero_ca': '12345', 'ativo': 'on', 'avisar_troca': 'on'})
        self.botina.refresh_from_db()
        self.assertTrue(self.botina.avisar_troca)

    def test_aviso_de_troca_so_dos_epis_marcados_e_vencidos(self):
        hoje = timezone.now().date()
        ficha = FichaEPI.objects.create(funcionario=self.funcionario, data_entrega=hoje - timedelta(days=60))
        ItemEPI.objects.create(ficha=ficha, item_catalogo=self.botina, data_vencimento=hoje - timedelta(days=2))
        ItemEPI.objects.create(ficha=ficha, item_catalogo=self.luva, data_vencimento=hoje - timedelta(days=2))
        ItemEPI.objects.create(ficha=ficha, item_catalogo=self.botina, data_vencimento=hoje + timedelta(days=5))
        ItemEPI.objects.create(ficha=ficha, item_catalogo=self.botina, data_vencimento=hoje - timedelta(days=9), devolvido=True)
        self.luva.avisar_troca = False
        self.luva.save()

        self.assertEqual([i.item_catalogo for i in ItemEPI.com_aviso_de_troca()], [self.botina])

        self.client.force_login(self.user_seguranca)
        response = self.client.get(reverse('listar_epis_vencendo'))
        self.assertContains(response, 'EPI Botina do funcionário Operador está vencido.')
        self.assertNotContains(response, 'EPI Luva do funcionário')
        self.assertEqual(response.context['badge_trocas_epi'], 1)

    def test_registra_e_filtra_ocorrencia(self):
        self.client.force_login(self.user_seguranca)
        self.client.post(reverse('criar_ocorrencia_epi'), {
            'funcionario': self.funcionario.id, 'data_ocorrencia': '2026-10-01',
            'descricao': 'Sem óculos na linha.', 'item_catalogo': '', 'tipo': 'aviso',
        })
        ocorrencia = OcorrenciaEPI.objects.get()
        self.assertEqual(ocorrencia.tecnico, self.user_seguranca)

        url = reverse('listar_ocorrencias_epi')
        self.assertEqual(len(self.client.get(url, {'de': '2026-10-01'}).context['ocorrencias']), 1)
        self.assertEqual(len(self.client.get(url, {'de': '2026-10-02'}).context['ocorrencias']), 0)
        self.assertEqual(self.client.get(url, {'de': 'lixo'}).status_code, 200)


def _foto_png():
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new('RGB', (2, 2), 'white').save(buffer, 'PNG')
    return SimpleUploadedFile('luva.png', buffer.getvalue(), content_type='image/png')


@override_settings(MEDIA_ROOT=MEDIA_TESTE)
class TrocaSolicitadaEPITests(TestCase):
    """Colaborador pede troca de EPI inutilizado; Segurança/RH confirma (nova ficha) ou recusa."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='Produção')
        self.user_seguranca = _criar_usuario('Tecnico SST', '33333333333', 'seguranca_trabalho', self.setor)
        self.user_colaborador = _criar_usuario('Operador', '44444444444', 'colaborador', self.setor)
        self.user_outro = _criar_usuario('Outro', '55555555555', 'colaborador', self.setor)
        self.funcionario = Funcionario.objects.get(cpf='44444444444')
        self.luva = ItemCatalogoEPI.objects.create(nome='Luva', numero_ca='67890')
        ficha = FichaEPI.objects.create(funcionario=self.funcionario, status='ativo', data_entrega=date(2026, 10, 1))
        self.item = ItemEPI.objects.create(ficha=ficha, item_catalogo=self.luva, quantidade=2)

    def _pedir(self, foto=True, user=None):
        self.client.force_login(user or self.user_colaborador)
        dados = {'motivo': 'Rasgou na palma.'}
        if foto:
            dados['foto'] = _foto_png()
        return self.client.post(reverse('solicitar_troca_epi', args=[self.item.id]), dados)

    def test_colaborador_ve_seus_epis_e_pede_troca_com_foto(self):
        self.client.force_login(self.user_colaborador)
        self.assertContains(self.client.get(reverse('meus_epis')), 'Solicitar troca')
        self.assertContains(self.client.get(reverse('meus_documentos')), reverse('meus_epis'))

        self._pedir()
        solicitacao = SolicitacaoTrocaEPI.objects.get()
        self.assertEqual((solicitacao.status, solicitacao.funcionario), ('pendente', self.funcionario))
        self.assertTrue(solicitacao.foto)
        self.assertContains(self.client.get(reverse('meus_epis')), 'Troca em análise')

    def test_nao_duplica_pedido_em_aberto(self):
        self._pedir()
        self._pedir(foto=False)
        self.assertEqual(SolicitacaoTrocaEPI.objects.count(), 1)

    def test_nao_pede_troca_de_epi_de_outro(self):
        self._pedir(user=self.user_outro)
        self.assertFalse(SolicitacaoTrocaEPI.objects.exists())

    def test_foto_so_para_o_dono_e_quem_opera_o_modulo(self):
        self._pedir()
        url = reverse('ver_foto_troca_epi', args=[SolicitacaoTrocaEPI.objects.get().id])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.user_seguranca)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.force_login(self.user_outro)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_confirmar_gera_nova_ficha_e_tira_o_antigo_de_uso(self):
        self._pedir()
        solicitacao = SolicitacaoTrocaEPI.objects.get()
        self.client.force_login(self.user_seguranca)
        self.assertContains(self.client.get(reverse('listar_trocas_epi')), 'Rasgou na palma.')

        self.client.post(reverse('confirmar_troca_epi', args=[solicitacao.id]), {
            f'troca{solicitacao.id}-quantidade': '2', f'troca{solicitacao.id}-data_vencimento': '2027-01-10',
        })
        solicitacao.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(solicitacao.status, 'aprovada')
        self.assertTrue(self.item.devolvido)
        nova = solicitacao.nova_ficha
        self.assertEqual((nova.funcionario, nova.status), (self.funcionario, 'aguardando_assinatura'))
        self.assertTrue(nova.arquivo_gerado)
        novo_item = nova.itens.get()
        self.assertEqual((novo_item.item_catalogo, novo_item.quantidade, novo_item.data_vencimento),
                         (self.luva, 2, date(2027, 1, 10)))

    def test_recusar_exige_motivo_e_mostra_ao_colaborador(self):
        self._pedir(foto=False)
        solicitacao = SolicitacaoTrocaEPI.objects.get()
        self.client.force_login(self.user_seguranca)
        self.client.post(reverse('recusar_troca_epi', args=[solicitacao.id]), {'motivo_recusa': ''})
        solicitacao.refresh_from_db()
        self.assertEqual(solicitacao.status, 'pendente')

        self.client.post(reverse('recusar_troca_epi', args=[solicitacao.id]), {'motivo_recusa': 'Desgaste normal de uso.'})
        solicitacao.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(solicitacao.status, 'recusada')
        self.assertFalse(self.item.devolvido)

        self.client.force_login(self.user_colaborador)
        self.assertContains(self.client.get(reverse('meus_epis')), 'Desgaste normal de uso.')

    def test_colaborador_nao_confirma_nem_recusa(self):
        self._pedir(foto=False)
        solicitacao = SolicitacaoTrocaEPI.objects.get()
        self.client.post(reverse('confirmar_troca_epi', args=[solicitacao.id]), {f'troca{solicitacao.id}-quantidade': '1'})
        self.client.post(reverse('recusar_troca_epi', args=[solicitacao.id]), {'motivo_recusa': 'x'})
        solicitacao.refresh_from_db()
        self.assertEqual(solicitacao.status, 'pendente')

    def test_pedido_pendente_aparece_no_menu_da_seguranca(self):
        self._pedir(foto=False)
        self.client.force_login(self.user_seguranca)
        response = self.client.get(reverse('listar_fichas_epi'))
        self.assertEqual(response.context['badge_trocas_solicitadas_epi'], 1)
        self.assertContains(response, reverse('listar_trocas_epi'))


@override_settings(MEDIA_ROOT=MEDIA_TESTE)
class OcorrenciaAvisoOuAdvertenciaTests(TestCase):
    """Aviso só informa; advertência vai ao gestor e, aprovada, tira assiduidade e V.A do mês."""

    def setUp(self):
        self.setor = Setor.objects.create(nome='Produção')
        self.user_seguranca = _criar_usuario('Tecnico SST', '33333333333', 'seguranca_trabalho', self.setor)
        self.user_colaborador = _criar_usuario('Operador', '44444444444', 'colaborador', self.setor)
        self.user_gestor = _criar_usuario('Gestora', '55555555555', 'gestor', self.setor)
        outro_setor = Setor.objects.create(nome='Expedição')
        self.user_gestor_outro = _criar_usuario('Outro Gestor', '66666666666', 'gestor', outro_setor)
        self.funcionario = Funcionario.objects.get(cpf='44444444444')

    def _registrar(self, tipo, funcionario=None, data='2026-10-05'):
        self.client.force_login(self.user_seguranca)
        return self.client.post(reverse('criar_ocorrencia_epi'), {
            'funcionario': (funcionario or self.funcionario).id, 'data_ocorrencia': data,
            'tipo': tipo, 'descricao': 'Sem óculos na linha.', 'item_catalogo': '',
        })

    def test_aviso_nao_vai_para_aprovacao_e_aparece_para_colaborador(self):
        self._registrar('aviso')
        ocorrencia = OcorrenciaEPI.objects.get()
        self.assertEqual((ocorrencia.tipo, ocorrencia.status_advertencia), ('aviso', ''))

        self.client.force_login(self.user_gestor)
        response = self.client.get(reverse('ocorrencias_equipe'))
        self.assertEqual(list(response.context['pendentes']), [])
        self.assertContains(response, 'Sem óculos na linha.')

        self.client.force_login(self.user_colaborador)
        self.assertContains(self.client.get(reverse('meus_epis')), 'Aviso de 05/10/2026')

    def test_advertencia_espera_gestor_e_colaborador_so_ve_depois_de_aprovada(self):
        self._registrar('advertencia')
        ocorrencia = OcorrenciaEPI.objects.get()
        self.assertEqual(ocorrencia.status_advertencia, 'pendente_gestor')

        self.client.force_login(self.user_colaborador)
        self.assertNotContains(self.client.get(reverse('meus_epis')), 'Sem óculos na linha.')

        self.client.force_login(self.user_gestor)
        response = self.client.get(reverse('ocorrencias_equipe'))
        self.assertEqual(list(response.context['pendentes']), [ocorrencia])
        self.assertEqual(response.context['badge_advertencias_epi'], 1)

        self.client.post(reverse('aprovar_advertencia_epi', args=[ocorrencia.id]))
        ocorrencia.refresh_from_db()
        self.assertEqual((ocorrencia.status_advertencia, ocorrencia.decidido_por), ('aprovada', self.user_gestor))

        # Mês da perda = mês em que a ocorrência foi registrada (hoje, no teste).
        mes = timezone.localdate().strftime('%m/%Y')
        self.client.force_login(self.user_colaborador)
        self.assertContains(self.client.get(reverse('meus_epis')), f'perde a assiduidade e o vale-alimentação de {mes}')

    def test_gestor_recusa_com_motivo(self):
        self._registrar('advertencia')
        ocorrencia = OcorrenciaEPI.objects.get()
        self.client.force_login(self.user_gestor)
        self.client.post(reverse('recusar_advertencia_epi', args=[ocorrencia.id]), {'motivo_recusa': ''})
        ocorrencia.refresh_from_db()
        self.assertEqual(ocorrencia.status_advertencia, 'pendente_gestor')

        self.client.post(reverse('recusar_advertencia_epi', args=[ocorrencia.id]), {'motivo_recusa': 'Primeira vez.'})
        ocorrencia.refresh_from_db()
        self.assertEqual((ocorrencia.status_advertencia, ocorrencia.motivo_recusa), ('recusada', 'Primeira vez.'))
        self.client.force_login(self.user_seguranca)
        self.assertContains(self.client.get(reverse('listar_ocorrencias_epi')), 'Primeira vez.')

    def test_so_o_gestor_da_equipe_decide(self):
        self._registrar('advertencia')
        ocorrencia = OcorrenciaEPI.objects.get()
        for user in (self.user_gestor_outro, self.user_seguranca, self.user_colaborador):
            self.client.force_login(user)
            self.client.post(reverse('aprovar_advertencia_epi', args=[ocorrencia.id]))
        ocorrencia.refresh_from_db()
        self.assertEqual(ocorrencia.status_advertencia, 'pendente_gestor')

    def test_advertencia_sem_gestor_para_aprovar_e_recusada_no_registro(self):
        sem_gestor = Funcionario.objects.create(
            nome='Sem Gestor', cpf='77777777777', cargo='Op', setor=Setor.objects.create(nome='Moinho'), status='ativo',
        )
        response = self._registrar('advertencia', funcionario=sem_gestor)
        self.assertContains(response, 'não tem gestor no sistema')
        self.assertFalse(OcorrenciaEPI.objects.exists())
        self._registrar('aviso', funcionario=sem_gestor)
        self.assertEqual(OcorrenciaEPI.objects.count(), 1)

    def test_advertencia_aprovada_tira_assiduidade_e_va_do_mes_do_registro(self):
        from datetime import datetime
        from folha import services
        # Ocorrência de outubro lançada em novembro: a perda é de novembro (mês do registro).
        self._registrar('advertencia', data='2026-10-28')
        OcorrenciaEPI.objects.update(criado_em=timezone.make_aware(datetime(2026, 11, 3, 10, 0)))
        ocorrencia = OcorrenciaEPI.objects.get()
        outubro, novembro = date(2026, 10, 1), date(2026, 11, 1)

        valores = services.calcular_ponto(self.funcionario, novembro)['valores']
        self.assertTrue(valores['assiduidade'] and valores['vale_alimentacao'])  # pendente não conta

        fechamento = services.abrir_fechamento(novembro, None)
        services.recalcular_fechamento(fechamento)
        linha = fechamento.linhas.get(funcionario=self.funcionario)
        self.assertTrue(linha.assiduidade)

        self.client.force_login(self.user_gestor)
        self.client.post(reverse('aprovar_advertencia_epi', args=[ocorrencia.id]))
        linha.refresh_from_db()
        self.assertEqual((linha.assiduidade, linha.vale_alimentacao), (False, False))  # fechamento aberto: recalculou

        valores = services.calcular_ponto(self.funcionario, outubro)['valores']
        self.assertEqual((valores['assiduidade'], valores['vale_alimentacao']), (True, True))

    def _advertencia_aprovada(self):
        self._registrar('advertencia')
        ocorrencia = OcorrenciaEPI.objects.get()
        self.client.force_login(self.user_gestor)
        self.client.post(reverse('aprovar_advertencia_epi', args=[ocorrencia.id]))
        ocorrencia.refresh_from_db()
        return ocorrencia

    def _enviar_termo_assinado(self, ocorrencia, user=None):
        self.client.force_login(user or self.user_colaborador)
        return self.client.post(
            reverse('reenviar_advertencia_assinada', args=[ocorrencia.id]),
            {'arquivo_assinado': SimpleUploadedFile('assinado.pdf', b'%PDF-1.4', content_type='application/pdf')},
        )

    def test_aprovar_gera_termo_que_o_colaborador_assina_e_a_seguranca_valida(self):
        ocorrencia = self._advertencia_aprovada()
        self.assertEqual(ocorrencia.status_assinatura, 'aguardando_assinatura')
        self.assertTrue(ocorrencia.arquivo_advertencia)

        self.client.force_login(self.user_colaborador)
        url_envio = reverse('reenviar_advertencia_assinada', args=[ocorrencia.id])
        self.assertContains(self.client.get(reverse('meus_documentos')), url_envio)
        self.assertContains(self.client.get(reverse('minhas_assinaturas')), url_envio)
        self.assertEqual(self.client.get(reverse('ver_advertencia_epi', args=[ocorrencia.id, 'gerado'])).status_code, 200)

        self._enviar_termo_assinado(ocorrencia)
        ocorrencia.refresh_from_db()
        self.assertEqual(ocorrencia.status_assinatura, 'em_validacao')

        self.client.force_login(self.user_seguranca)
        response = self.client.get(reverse('listar_ocorrencias_epi'))
        self.assertEqual(response.context['badge_advertencias_assinadas_epi'], 1)
        self.assertContains(response, reverse('aprovar_assinatura_advertencia', args=[ocorrencia.id]))

        self.client.post(reverse('aprovar_assinatura_advertencia', args=[ocorrencia.id]))
        ocorrencia.refresh_from_db()
        self.assertEqual((ocorrencia.status_assinatura, ocorrencia.assinatura_validada_por), ('assinada', self.user_seguranca))

    def test_assinatura_rejeitada_volta_para_o_colaborador(self):
        ocorrencia = self._advertencia_aprovada()
        self._enviar_termo_assinado(ocorrencia)
        self.client.force_login(self.user_seguranca)
        self.client.post(reverse('rejeitar_assinatura_advertencia', args=[ocorrencia.id]), {'motivo': 'Falta a assinatura.'})
        ocorrencia.refresh_from_db()
        self.assertEqual((ocorrencia.status_assinatura, ocorrencia.motivo_rejeicao_assinatura),
                         ('aguardando_assinatura', 'Falta a assinatura.'))
        self.client.force_login(self.user_colaborador)
        self.assertContains(self.client.get(reverse('meus_documentos')), 'Falta a assinatura.')

    def test_termo_so_do_proprio_colaborador(self):
        ocorrencia = self._advertencia_aprovada()
        intruso = _criar_usuario('Intruso', '88888888888', 'colaborador', self.setor)
        self._enviar_termo_assinado(ocorrencia, user=intruso)
        ocorrencia.refresh_from_db()
        self.assertEqual(ocorrencia.status_assinatura, 'aguardando_assinatura')
        self.assertEqual(self.client.get(reverse('ver_advertencia_epi', args=[ocorrencia.id, 'gerado'])).status_code, 404)

    def test_aviso_nao_gera_termo(self):
        self._registrar('aviso')
        ocorrencia = OcorrenciaEPI.objects.get()
        self.assertEqual((ocorrencia.status_assinatura, bool(ocorrencia.arquivo_advertencia)), ('', False))

    def test_foto_da_ocorrencia_para_gestor_da_equipe(self):
        ocorrencia = OcorrenciaEPI.objects.create(
            funcionario=self.funcionario, data_ocorrencia=date(2026, 10, 5), descricao='x',
            foto=ContentFile(b'x', name='foto.png'),
        )
        url = reverse('ver_foto_ocorrencia_epi', args=[ocorrencia.id])
        for user, esperado in ((self.user_gestor, 200), (self.user_colaborador, 200), (self.user_gestor_outro, 404)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, esperado)
