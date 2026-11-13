"""
O que fica guardado fora do servidor:
- nenhum cache no meio do caminho (Cloudflare) guarda resposta do sistema
  (`SoCacheDoNavegadorMiddleware`);
- a tela que mostra uma senha gerada não volta pelo "voltar" (`never_cache`);
- o app do celular só guarda /static/, nunca arquivo de pessoa.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from terceiros.tests import _criar_empresa, _criar_responsavel

from .models import Funcionario, Setor, Usuario


class CacheTests(TestCase):
    def setUp(self):
        setor = Setor.objects.create(nome='RH')
        self.rh = self._conta('10000000001', 'rh', setor)
        self.colaborador = self._conta('20000000001', 'colaborador', setor)

    def _conta(self, cpf, papel, setor):
        funcionario = Funcionario.objects.create(nome=f'Pessoa {cpf}', cpf=cpf, cargo='Cargo', setor=setor, status='ativo')
        user = User.objects.create_user(username=cpf, password='senha-forte-123')
        return Usuario.objects.create(user=user, funcionario=funcionario, papel=papel)

    def test_paginas_e_redirects_saem_como_private(self):
        anonimo = self.client.get(reverse('folha:exportar_xlsx', args=[1]))
        self.assertEqual(anonimo.status_code, 302)  # o Cloudflare guardaria este redirect por 20 min
        self.assertEqual(anonimo['Cache-Control'], 'private')

        self.client.force_login(self.rh.user)
        self.assertEqual(self.client.get(reverse('dashboard'))['Cache-Control'], 'private')

    def test_quem_ja_define_o_cache_continua_mandando(self):
        resposta = self.client.get(reverse('service_worker'))  # never_cache na view

        self.assertIn('no-store', resposta['Cache-Control'])

    def test_telas_de_senha_gerada_nao_ficam_no_navegador(self):
        self.client.force_login(self.rh.user)
        sem_conta = Funcionario.objects.create(
            nome='Sem Conta', cpf='30000000001', cargo='Cargo', setor=self.rh.funcionario.setor, status='ativo',
        )
        responsavel = _criar_responsavel(_criar_empresa(status='ativo'))

        telas = {
            'redefinir senha': reverse('resetar_senha_funcionario', args=[self.colaborador.funcionario_id]),
            'gerar acesso': reverse('gerar_acesso_funcionario', args=[sem_conta.id]),
            'senha do terceiro': reverse('terceiros:resetar_senha_responsavel', args=[responsavel.empresa_id]),
        }
        for tela, url in telas.items():
            with self.subTest(tela=tela):
                resposta = self.client.post(url)
                self.assertEqual(resposta.status_code, 200)
                self.assertIn('senha_gerada', resposta.context)
                self.assertIn('no-store', resposta['Cache-Control'])

    def test_app_do_celular_nao_guarda_arquivo_de_pessoa(self):
        script = self.client.get(reverse('service_worker')).content.decode('utf-8')

        self.assertNotIn("startsWith('/media/')", script)
        # Versão nova: o `activate` apaga dos aparelhos o cache antigo, que tinha fotos.
        self.assertIn("'sistema-rh-estatico-v3'", script)
