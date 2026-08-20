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

    document.querySelectorAll('[data-toggle-alvo]').forEach(function (botao) {
        var textoOriginal = botao.textContent;
        botao.addEventListener('click', function () {
            var alvo = document.getElementById(botao.dataset.toggleAlvo);
            if (!alvo) return;
            var vaiAbrir = alvo.style.display === 'none';
            alvo.style.display = vaiAbrir ? '' : 'none';
            botao.textContent = vaiAbrir ? (botao.dataset.textoAlternativo || textoOriginal) : textoOriginal;
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

    // Formulário de "Definir jornada": quando o horário escolhido é 12x36, a
    // data vira "dia de trabalho de referência do ciclo" em vez de "a partir
    // de" -- evita expor qualquer coisa de par/ímpar/cálculo pro usuário.
    document.querySelectorAll('[data-jornada-form]').forEach(function (form) {
        var select = form.querySelector('select[name="horario"]');
        var label = form.querySelector('[data-jornada-label-data]');
        var ajuda = form.parentElement.querySelector('[data-jornada-ajuda]');
        if (!select || !label) return;

        var textoOriginalLabel = label.textContent;
        var textoOriginalAjuda = ajuda ? ajuda.textContent : '';

        function atualizar() {
            var opcao = select.options[select.selectedIndex];
            var eh12x36 = !!(opcao && opcao.dataset.doze36 === '1');
            label.textContent = eh12x36 ? 'Dia de trabalho para referência do ciclo' : textoOriginalLabel;
            if (ajuda) {
                ajuda.textContent = eh12x36
                    ? 'Selecione um dia em que este funcionário trabalha. O sistema calculará automaticamente os próximos dias de trabalho e folga.'
                    : textoOriginalAjuda;
            }
        }

        select.addEventListener('change', atualizar);
        atualizar();
    });
});
