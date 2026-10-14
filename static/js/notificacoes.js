// Notificações push: pede permissão, inscreve o aparelho no servidor e
// mantém a inscrição em nome de quem está logado. O envio fica em
// funcionarios/notificacoes.py e a exibição no service worker.
//
// Na página:
//   [data-push-estado]    texto com a situação neste aparelho
//   [data-push-ativar]    botão que pede a permissão e inscreve
//   [data-push-desativar] botão que cancela a inscrição deste aparelho
//   #aviso-push           convite que aparece até a pessoa responder
(function () {
    'use strict';

    var script = document.currentScript;
    var CHAVE_PUBLICA = script.dataset.chave;
    var URL_INSCREVER = script.dataset.urlInscrever;
    var URL_CANCELAR = script.dataset.urlCancelar;
    var CSRF = script.dataset.csrf;
    var INSCRITO_NA_SESSAO = script.dataset.inscritoNaSessao === '1';

    // Marca, só neste aparelho, que a pessoa desligou pelo botão. A permissão
    // do navegador continua concedida, então sem isso a sincronização
    // automática inscreveria de novo no carregamento seguinte.
    var CHAVE_DESATIVADO = 'push-desativado';
    var CHAVE_AVISO_DISPENSADO = 'push-aviso-dispensado';

    var suportado = 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
    var ehIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
        (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    var instalado = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

    function lerLocal(chave) {
        try { return localStorage.getItem(chave); } catch (e) { return null; }
    }

    function gravarLocal(chave, valor) {
        try {
            if (valor === null) localStorage.removeItem(chave);
            else localStorage.setItem(chave, valor);
        } catch (e) { /* modo privado: só não lembra */ }
    }

    function chaveEmBytes(base64url) {
        var preenchimento = '='.repeat((4 - (base64url.length % 4)) % 4);
        var base64 = (base64url + preenchimento).replace(/-/g, '+').replace(/_/g, '/');
        var bruto = atob(base64);
        var bytes = new Uint8Array(bruto.length);
        for (var i = 0; i < bruto.length; i++) bytes[i] = bruto.charCodeAt(i);
        return bytes;
    }

    function mesmaChave(inscricao) {
        var atual = inscricao.options && inscricao.options.applicationServerKey;
        if (!atual) return true;
        var esperada = chaveEmBytes(CHAVE_PUBLICA);
        var recebida = new Uint8Array(atual);
        if (recebida.length !== esperada.length) return false;
        for (var i = 0; i < esperada.length; i++) {
            if (recebida[i] !== esperada[i]) return false;
        }
        return true;
    }

    function postar(url, corpo) {
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
            body: JSON.stringify(corpo),
        }).then(function (resposta) {
            if (!resposta.ok) throw new Error('Servidor respondeu ' + resposta.status);
        });
    }

    function inscricaoAtual() {
        return navigator.serviceWorker.ready.then(function (registro) {
            return registro.pushManager.getSubscription();
        });
    }

    function inscrever() {
        return navigator.serviceWorker.ready.then(function (registro) {
            return registro.pushManager.getSubscription().then(function (inscricao) {
                // Chave VAPID trocada no servidor: a inscrição antiga não
                // recebe mais nada, precisa ser refeita.
                if (inscricao && !mesmaChave(inscricao)) {
                    return inscricao.unsubscribe().then(function () { return null; });
                }
                return inscricao;
            }).then(function (inscricao) {
                return inscricao || registro.pushManager.subscribe({
                    userVisibleOnly: true,
                    applicationServerKey: chaveEmBytes(CHAVE_PUBLICA),
                });
            });
        }).then(function (inscricao) {
            return postar(URL_INSCREVER, inscricao.toJSON());
        });
    }

    function ativar() {
        gravarLocal(CHAVE_DESATIVADO, null);
        return Notification.requestPermission().then(function (permissao) {
            if (permissao === 'granted') return inscrever();
        }).catch(function (erro) {
            console.warn('Não foi possível ativar as notificações:', erro);
        }).then(atualizarTela);
    }

    function desativar() {
        gravarLocal(CHAVE_DESATIVADO, '1');
        return inscricaoAtual().then(function (inscricao) {
            if (!inscricao) return;
            return postar(URL_CANCELAR, { endpoint: inscricao.endpoint }).then(function () {
                return inscricao.unsubscribe();
            });
        }).catch(function (erro) {
            console.warn('Não foi possível desativar as notificações:', erro);
        }).then(atualizarTela);
    }

    function mostrar(seletor, visivel) {
        document.querySelectorAll(seletor).forEach(function (el) { el.hidden = !visivel; });
    }

    function escreverEstado(texto) {
        document.querySelectorAll('[data-push-estado]').forEach(function (el) { el.textContent = texto; });
    }

    function atualizarTela() {
        var aviso = document.getElementById('aviso-push');

        if (!suportado) {
            escreverEstado(ehIOS && !instalado
                ? 'No iPhone, as notificações só funcionam com o app instalado: toque em Compartilhar, ' +
                  'depois em "Adicionar à Tela de Início", e abra o app por lá.'
                : 'Este navegador não recebe notificações.');
            mostrar('[data-push-ativar], [data-push-desativar]', false);
            if (aviso) aviso.hidden = true;
            return Promise.resolve();
        }

        var permissao = Notification.permission;
        if (aviso) {
            aviso.hidden = !(permissao === 'default' && !lerLocal(CHAVE_AVISO_DISPENSADO));
        }

        if (permissao === 'denied') {
            escreverEstado('As notificações estão bloqueadas para este site nas configurações do navegador.');
            mostrar('[data-push-ativar], [data-push-desativar]', false);
            return Promise.resolve();
        }

        return inscricaoAtual().then(function (inscricao) {
            var ativo = permissao === 'granted' && inscricao && !lerLocal(CHAVE_DESATIVADO);
            escreverEstado(ativo ? 'Ativadas neste aparelho.' : 'Desativadas neste aparelho.');
            mostrar('[data-push-ativar]', !ativo);
            mostrar('[data-push-desativar]', !!ativo);
        });
    }

    document.addEventListener('click', function (evento) {
        var alvo = evento.target.closest('[data-push-ativar], [data-push-desativar], [data-push-dispensar]');
        if (!alvo) return;
        evento.preventDefault();
        if (alvo.hasAttribute('data-push-ativar')) {
            ativar();
        } else if (alvo.hasAttribute('data-push-desativar')) {
            desativar();
        } else {
            gravarLocal(CHAVE_AVISO_DISPENSADO, '1');
            var aviso = document.getElementById('aviso-push');
            if (aviso) aviso.hidden = true;
        }
    });

    document.addEventListener('DOMContentLoaded', function () {
        atualizarTela();

        // Permissão já dada: refaz o vínculo uma vez por sessão. Cobre o
        // aparelho compartilhado (o logout desvincula quem saiu) e a
        // inscrição que o navegador renovou sozinho.
        if (suportado && Notification.permission === 'granted' &&
                !INSCRITO_NA_SESSAO && !lerLocal(CHAVE_DESATIVADO)) {
            inscrever().then(atualizarTela).catch(function (erro) {
                console.warn('Não foi possível sincronizar as notificações:', erro);
            });
        }
    });
})();
