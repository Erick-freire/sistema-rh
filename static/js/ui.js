document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('tr[data-href]').forEach(function (linha) {
        linha.addEventListener('click', function (evento) {
            if (evento.target.closest('a, button')) {
                return;
            }
            window.location.href = linha.dataset.href;
        });
    });

    document.querySelectorAll('[data-copy]').forEach(function (botao) {
        var htmlOriginal = botao.innerHTML;
        botao.addEventListener('click', function () {
            var alvo = document.getElementById(botao.dataset.copy);
            if (!alvo) return;
            navigator.clipboard.writeText(alvo.textContent.trim());
            botao.textContent = 'Copiado!';
            setTimeout(function () { botao.innerHTML = htmlOriginal; }, 1500);
        });
    });

    document.querySelectorAll('[data-toggle-senha]').forEach(function (botao) {
        botao.addEventListener('click', function () {
            var alvo = document.getElementById(botao.dataset.toggleSenha);
            if (!alvo) return;
            alvo.type = alvo.type === 'password' ? 'text' : 'password';
        });
    });

    // Formulários que podem demorar bastante (ex.: Sincronizar do Ponto, que
    // depende de equipamentos às vezes lentos) -- mostra uma tela de
    // carregando em vez de deixar a página parecendo travada até o redirect
    // final trazer a mensagem de sucesso/erro.
    var overlay = document.getElementById('overlay-carregando');
    var overlayTexto = document.getElementById('overlay-carregando-texto');
    document.querySelectorAll('form[data-loading-form]').forEach(function (form) {
        form.addEventListener('submit', function () {
            if (!overlay) return;
            overlayTexto.textContent = form.dataset.loadingMessage || 'Processando...';
            overlay.classList.add('is-open');
            form.querySelectorAll('button[type="submit"]').forEach(function (botao) {
                botao.disabled = true;
            });
        });
    });
    // Se o navegador restaurar a página do cache (ex.: botão "voltar") depois
    // de um redirect, garante que o overlay não fique preso aberto.
    window.addEventListener('pageshow', function () {
        if (overlay) overlay.classList.remove('is-open');
    });
});
