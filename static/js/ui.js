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
});
