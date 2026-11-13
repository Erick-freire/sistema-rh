"""
Relógio de ponto: a senha de admin do equipamento vai, a cada teste e a cada
sincronização, para o IP cadastrado. Quem troca o IP decide para onde ela vai.

- Configurar relógio (cadastrar, editar, testar, ativar/desativar, excluir) é
  só da TI, que no sistema é a conta de administrador (superuser ou "membro da
  equipe" no /admin/). A senha dessas contas o RH não redefine pelo app.
- RH e DP recebem as batidas e enviam os cadastros.
- IP só da rede interna, inclusive pelo /admin/ (validação no model).
"""
from unittest import mock

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from funcionarios.models import Funcionario, Setor, Usuario

from .forms import EquipamentoForm
from .models import Equipamento


def _equipamento(**kwargs):
    dados = dict(nome='Relógio Portaria', ip='192.168.0.50', porta=443, usuario_admin='admin', senha_admin='segredo')
    dados.update(kwargs)
    return Equipamento.objects.create(**dados)


class RelogiosSoTiTests(TestCase):
    def setUp(self):
        self.setor = Setor.objects.create(nome='Geral')
        self.rh = self._conta('10000000001', 'rh')
        self.dp = self._conta('10000000002', 'dp')
        self.gerente_rh = self._conta('10000000003', 'gestor', acesso_rh=True)
        self.ti = self._conta('10000000004', 'colaborador', is_staff=True)
        self.superuser = self._conta('10000000005', 'gestor', is_superuser=True)
        self.gestor = self._conta('10000000006', 'gestor')
        self.equipamento = _equipamento()

    def _conta(self, cpf, papel, acesso_rh=False, **flags):
        funcionario = Funcionario.objects.create(
            nome=f'Pessoa {cpf}', cpf=cpf, cargo='Cargo', setor=self.setor, status='ativo',
        )
        user = User.objects.create_user(username=cpf, password='senha-forte-123', **flags)
        Usuario.objects.create(user=user, funcionario=funcionario, papel=papel, acesso_rh=acesso_rh)
        return user

    def _form_de_edicao(self, **mudancas):
        dados = {
            'nome': self.equipamento.nome, 'ip': self.equipamento.ip, 'porta': self.equipamento.porta,
            'usa_https': 'on', 'modelo': '', 'local': '', 'numero_serie': '', 'ativo': 'on',
            'usuario_admin': 'admin', 'senha_admin': '',
        }
        dados.update(mudancas)
        return dados

    @mock.patch('ponto.services.coletar_marcacoes', return_value=0)
    @mock.patch('ponto.services.processar_pendencias', return_value=(0, 0))
    def test_rh_e_dp_recebem_batidas_e_enviam_cadastros(self, mock_enviar, mock_receber):
        lista = reverse('ponto:listar_equipamentos')
        for nome, user in (('rh', self.rh), ('dp', self.dp), ('gerente do RH', self.gerente_rh)):
            with self.subTest(conta=nome):
                self.client.force_login(user)
                mock_enviar.reset_mock()
                mock_receber.reset_mock()

                self.assertEqual(self.client.get(lista).status_code, 200)
                for acao in ('ponto:receber_equipamento', 'ponto:enviar_equipamento'):
                    self.assertRedirects(self.client.post(reverse(acao, args=[self.equipamento.id])), lista)
                for acao in ('ponto:receber_todos', 'ponto:enviar_todos'):
                    self.assertRedirects(self.client.post(reverse(acao)), lista)

                self.assertEqual(mock_receber.call_count, 2)
                self.assertEqual(mock_enviar.call_count, 2)

    @mock.patch('ponto.services.testar_conexao', return_value=(True, None))
    def test_rh_e_dp_nao_configuram_relogio(self, mock_testar):
        lista = reverse('ponto:listar_equipamentos')
        eq = self.equipamento
        for nome, user in (('rh', self.rh), ('dp', self.dp), ('gerente do RH', self.gerente_rh)):
            with self.subTest(conta=nome):
                self.client.force_login(user)

                # O ataque: apontar o relógio para um servidor de fora e testar a conexão.
                editar = reverse('ponto:editar_equipamento', args=[eq.id])
                self.assertRedirects(self.client.post(editar, self._form_de_edicao(ip='192.168.0.99')), lista)
                self.assertRedirects(self.client.get(editar), lista)
                self.assertRedirects(self.client.get(reverse('ponto:cadastrar_equipamento')), lista)
                self.client.post(reverse('ponto:cadastrar_equipamento'), self._form_de_edicao(senha_admin='x'))
                for acao in ('testar_conexao_equipamento', 'alternar_status_equipamento', 'excluir_equipamento'):
                    resposta = self.client.post(reverse(f'ponto:{acao}', args=[eq.id]), follow=True)
                    self.assertContains(resposta, 'fica com a TI')

                eq.refresh_from_db()
                self.assertEqual(eq.ip, '192.168.0.50')
                self.assertTrue(eq.ativo)
                self.assertEqual(Equipamento.objects.count(), 1)
                mock_testar.assert_not_called()

    @mock.patch('ponto.services.testar_conexao', return_value=(True, None))
    def test_ti_configura_relogio(self, mock_testar):
        for nome, user in (('membro da equipe', self.ti), ('superuser', self.superuser)):
            with self.subTest(conta=nome):
                self.client.force_login(user)
                eq = _equipamento(nome=f'Relógio {nome}')

                self.assertEqual(self.client.get(reverse('ponto:cadastrar_equipamento')).status_code, 200)
                resposta = self.client.post(
                    reverse('ponto:editar_equipamento', args=[eq.id]),
                    self._form_de_edicao(nome='Relógio Novo Nome', ip='192.168.0.120'),
                )
                self.assertRedirects(resposta, reverse('ponto:listar_equipamentos'))
                eq.refresh_from_db()
                self.assertEqual((eq.nome, eq.ip), ('Relógio Novo Nome', '192.168.0.120'))
                self.assertEqual(eq.senha_admin, 'segredo')  # em branco mantém a senha

                self.client.post(reverse('ponto:testar_conexao_equipamento', args=[eq.id]))
                mock_testar.assert_called_with(eq)
                self.client.post(reverse('ponto:alternar_status_equipamento', args=[eq.id]))
                eq.refresh_from_db()
                self.assertFalse(eq.ativo)
                self.client.post(reverse('ponto:excluir_equipamento', args=[eq.id]))
                self.assertFalse(Equipamento.objects.filter(id=eq.id).exists())

    def test_senha_em_branco_mantem_e_preenchida_troca(self):
        """Antes de 13/11/2026, salvar com a senha em branco apagava a senha guardada."""
        self.client.force_login(self.ti)
        editar = reverse('ponto:editar_equipamento', args=[self.equipamento.id])

        self.client.post(editar, self._form_de_edicao(local='Portaria 2'))
        self.equipamento.refresh_from_db()
        self.assertEqual((self.equipamento.local, self.equipamento.senha_admin), ('Portaria 2', 'segredo'))

        self.client.post(editar, self._form_de_edicao(senha_admin='nova-senha'))
        self.equipamento.refresh_from_db()
        self.assertEqual(self.equipamento.senha_admin, 'nova-senha')

    def test_tela_do_rh_so_tem_receber_e_enviar(self):
        self.client.force_login(self.rh)

        resposta = self.client.get(reverse('ponto:listar_equipamentos'))

        self.assertNotContains(resposta, reverse('ponto:cadastrar_equipamento'))
        self.assertNotContains(resposta, reverse('ponto:editar_equipamento', args=[self.equipamento.id]))
        self.assertContains(resposta, reverse('ponto:receber_equipamento', args=[self.equipamento.id]))
        self.assertContains(resposta, reverse('ponto:enviar_equipamento', args=[self.equipamento.id]))

    def test_ti_tem_menu_proprio_para_os_relogios(self):
        self.client.force_login(self.ti)

        resposta = self.client.get(reverse('ponto:listar_equipamentos'))

        self.assertContains(resposta, 'Relógios de ponto')
        self.assertContains(resposta, reverse('ponto:cadastrar_equipamento'))
        self.assertContains(resposta, reverse('ponto:editar_equipamento', args=[self.equipamento.id]))
        self.assertNotContains(resposta, reverse('ponto:listar_calculo'))  # o resto do ponto continua do DP/RH

    def test_quem_nao_e_ti_nem_dp_rh_nao_ve_os_relogios(self):
        self.client.force_login(self.gestor)

        self.assertRedirects(self.client.get(reverse('ponto:listar_equipamentos')), reverse('dashboard'))
        self.assertRedirects(
            self.client.get(reverse('ponto:editar_equipamento', args=[self.equipamento.id])), reverse('dashboard'),
        )
        self.assertNotContains(self.client.get(reverse('dashboard')), 'Relógios de ponto')

    def test_rh_nao_redefine_a_senha_da_ti(self):
        """O que impede o RH de entrar como a TI para mexer no relógio (item 4 da auditoria)."""
        rh = self.rh.usuario
        for user in (self.ti, self.superuser):
            with self.subTest(conta=user.username):
                self.assertFalse(rh.pode_redefinir_senha_de(user.usuario))


class IpDaRedeInternaTests(TestCase):
    DENTRO = ['192.168.0.187', '192.168.255.1', '10.0.0.1', '10.20.30.40', '172.16.0.1', '172.31.255.254']
    FORA = [
        '8.8.8.8', '203.0.113.5', '127.0.0.1', '172.15.255.255', '172.32.0.1', '169.254.10.10',
        '0.0.0.0', '::1', 'fe80::1', '2001:db8::1', '::ffff:192.168.0.10',
    ]

    def _form(self, ip, instancia=None):
        return EquipamentoForm(data={
            'nome': 'Relógio', 'ip': ip, 'porta': 443, 'usa_https': 'on', 'ativo': 'on',
            'usuario_admin': 'admin', 'senha_admin': 'segredo',
        }, instance=instancia)

    def test_aceita_so_rede_interna(self):
        for ip in self.DENTRO:
            with self.subTest(ip=ip):
                self.assertTrue(self._form(ip).is_valid(), self._form(ip).errors)
        for ip in self.FORA:
            with self.subTest(ip=ip):
                form = self._form(ip)
                self.assertFalse(form.is_valid())
                self.assertIn('rede interna', str(form.errors['ip']))

    def test_vale_tambem_fora_da_tela_do_ponto(self):
        """O /admin/ valida pelo model: a mesma regra vale lá."""
        equipamento = _equipamento()
        equipamento.ip = '203.0.113.5'

        with self.assertRaises(ValidationError) as erro:
            equipamento.full_clean()

        self.assertIn('ip', erro.exception.message_dict)

    def test_relogio_ja_cadastrado_na_rede_interna_continua_valido(self):
        equipamento = _equipamento(ip='192.168.0.208')

        self.assertTrue(self._form('192.168.0.208', instancia=equipamento).is_valid())
