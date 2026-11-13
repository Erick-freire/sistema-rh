"""
Sessão (decisão de 13/11/2026, igual para toda conta): sai depois de 1 hora sem
uso, e o login vale no máximo 12 horas mesmo em uso contínuo.
"""
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from terceiros.tests import _criar_empresa, _criar_responsavel

from .middleware import CHAVE_INICIO_DO_LOGIN
from .models import Funcionario, Setor, Usuario

UMA_HORA = 60 * 60


class SessaoTests(TestCase):
    def setUp(self):
        setor = Setor.objects.create(nome='RH')
        self.rh = self._conta('10000000001', 'rh', setor)
        self.colaborador = self._conta('10000000002', 'colaborador', setor)
        self.ti = self._conta('10000000003', 'colaborador', setor, is_superuser=True, is_staff=True)

    def _conta(self, cpf, papel, setor, **flags):
        funcionario = Funcionario.objects.create(nome=f'Pessoa {cpf}', cpf=cpf, cargo='Cargo', setor=setor, status='ativo')
        user = User.objects.create_user(username=cpf, password='senha-forte-123', **flags)
        Usuario.objects.create(user=user, funcionario=funcionario, papel=papel)
        return user

    def _inicio_do_login(self, segundos_atras):
        sessao = self.client.session
        sessao[CHAVE_INICIO_DO_LOGIN] = time.time() - segundos_atras
        sessao.save()

    def _logado(self):
        return '_auth_user_id' in self.client.session

    def test_configuracao(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, UMA_HORA)
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)
        self.assertEqual(settings.DURACAO_MAXIMA_DO_LOGIN, 12 * UMA_HORA)

    def test_cada_requisicao_renova_a_hora(self):
        self.client.force_login(self.rh)
        chave = self.client.session.session_key
        Session.objects.filter(pk=chave).update(expire_date=timezone.now() + timedelta(minutes=5))

        resposta = self.client.get(reverse('dashboard'))

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.cookies[settings.SESSION_COOKIE_NAME]['max-age'], UMA_HORA)
        expira = Session.objects.get(pk=chave).expire_date
        self.assertGreater(expira, timezone.now() + timedelta(minutes=59))

    def test_uma_hora_sem_uso_encerra(self):
        self.client.force_login(self.rh)
        Session.objects.filter(pk=self.client.session.session_key).update(
            expire_date=timezone.now() - timedelta(seconds=1),
        )

        resposta = self.client.get(reverse('dashboard'))

        self.assertRedirects(resposta, f"{reverse('login')}?next={reverse('dashboard')}", fetch_redirect_response=False)

    def test_login_marca_a_hora_de_entrada(self):
        antes = time.time()
        self.client.force_login(self.rh)

        self.assertGreaterEqual(self.client.session[CHAVE_INICIO_DO_LOGIN], antes)

    def test_login_com_mais_de_12_horas_encerra_mesmo_em_uso(self):
        for nome, user in (('rh', self.rh), ('colaborador', self.colaborador), ('ti', self.ti)):
            with self.subTest(conta=nome):
                self.client.force_login(user)
                self._inicio_do_login(12 * UMA_HORA + 60)

                resposta = self.client.get(reverse('dashboard'))

                self.assertRedirects(
                    resposta, f"{reverse('login')}?next={reverse('dashboard')}", fetch_redirect_response=False,
                )
                self.assertFalse(self._logado())

    def test_vale_tambem_para_o_terceiro(self):
        self.client.force_login(_criar_responsavel(_criar_empresa(status='ativo')).user)
        self._inicio_do_login(12 * UMA_HORA + 60)

        self.client.get(reverse('login'))

        self.assertFalse(self._logado())

    def test_dentro_das_12_horas_continua(self):
        self.client.force_login(self.rh)
        self._inicio_do_login(11 * UMA_HORA)

        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
        self.assertTrue(self._logado())

    def test_sessao_aberta_antes_da_regra_comeca_a_contar_agora(self):
        self.client.force_login(self.rh)
        sessao = self.client.session
        del sessao[CHAVE_INICIO_DO_LOGIN]
        sessao.save()
        antes = time.time()

        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

        self.assertGreaterEqual(self.client.session[CHAVE_INICIO_DO_LOGIN], antes)

    def test_entrar_de_novo_zera_as_12_horas(self):
        self.client.force_login(self.rh)
        self._inicio_do_login(12 * UMA_HORA + 60)
        self.client.get(reverse('dashboard'))  # encerrada

        self.client.force_login(self.rh)

        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_grade_da_folha_avisa_quando_a_sessao_termina(self):
        """O salvar da célula é por fetch: sem isto, a pessoa via só "não foi possível salvar"."""
        caminho = settings.BASE_DIR / 'static' / 'js' / 'folha.js'

        script = caminho.read_text(encoding='utf-8')

        self.assertIn('resposta.redirected', script)
        self.assertIn('Sua sessão terminou', script)
