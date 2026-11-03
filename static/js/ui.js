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

    // Formulário editado direto na página (ex.: dados da ficha do funcionário):
    // mostra "Alterações não salvas" enquanto algum campo difere do que veio do
    // servidor e pede confirmação antes de sair da página sem salvar.
    // data-avisar-nao-salvo="pendente": o servidor devolveu o form com erro, então
    // o que está nos campos ainda não foi salvo, mesmo sem mexer em nada.
    document.querySelectorAll('form[data-avisar-nao-salvo]').forEach(function (form) {
        var aviso = form.querySelector('[data-aviso-nao-salvo]');
        var pendente = form.dataset.avisarNaoSalvo === 'pendente';
        var enviando = false;

        function alterado() {
            return pendente || Array.prototype.some.call(form.elements, function (campo) {
                if (campo.type === 'hidden' || campo.disabled) return false;
                if (campo.tagName === 'SELECT') {
                    return Array.prototype.some.call(campo.options, function (opcao) {
                        return opcao.selected !== opcao.defaultSelected;
                    });
                }
                if (campo.type === 'checkbox' || campo.type === 'radio') {
                    return campo.checked !== campo.defaultChecked;
                }
                return 'defaultValue' in campo && campo.value !== campo.defaultValue;
            });
        }

        function atualizar() {
            if (aviso) aviso.hidden = !alterado();
        }

        form.addEventListener('input', atualizar);
        form.addEventListener('change', atualizar);
        form.addEventListener('submit', function () { enviando = true; });
        window.addEventListener('beforeunload', function (evento) {
            if (enviando || !alterado()) return;
            evento.preventDefault();
            evento.returnValue = '';
        });
        atualizar();
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

    // Toasts (mensagens do Django messages) -- somem sozinhos depois de um tempo.
    document.querySelectorAll('.toast').forEach(function (toast) {
        setTimeout(function () {
            toast.classList.add('toast--fadeout');
            setTimeout(function () { toast.remove(); }, 350);
        }, 5000);
    });

    // Modal de confirmação genérico: qualquer elemento com data-confirmar
    // (botão de submit ou link) pede "tem certeza?" antes de executar a ação.
    var modalConfirmacao = document.getElementById('modal-confirmacao');
    if (modalConfirmacao) {
        var modalTitulo = document.getElementById('modal-titulo');
        var modalMensagem = document.getElementById('modal-mensagem');
        var modalConfirmar = document.getElementById('modal-confirmar');
        var modalCancelar = document.getElementById('modal-cancelar');
        var acaoConfirmada = null;

        function abrirModalConfirmacao(titulo, mensagem, acao) {
            modalTitulo.textContent = titulo;
            modalMensagem.textContent = mensagem;
            acaoConfirmada = acao;
            modalConfirmacao.hidden = false;
            modalConfirmar.focus();
        }

        function fecharModalConfirmacao() {
            modalConfirmacao.hidden = true;
            acaoConfirmada = null;
        }

        modalConfirmar.addEventListener('click', function () {
            if (acaoConfirmada) acaoConfirmada();
            fecharModalConfirmacao();
        });
        modalCancelar.addEventListener('click', fecharModalConfirmacao);
        modalConfirmacao.addEventListener('click', function (evento) {
            if (evento.target === modalConfirmacao) fecharModalConfirmacao();
        });
        document.addEventListener('keydown', function (evento) {
            if (!modalConfirmacao.hidden && evento.key === 'Escape') fecharModalConfirmacao();
        });

        document.addEventListener('click', function (evento) {
            var el = evento.target.closest('[data-confirmar]');
            if (!el) return;

            evento.preventDefault();
            var mensagem = el.dataset.confirmar;
            var titulo = el.dataset.confirmarTitulo || 'Confirmar ação';

            abrirModalConfirmacao(titulo, mensagem, function () {
                // `el.form` cobre o botão ligado a um form de fora pelo atributo form="..."
                // (ex.: grade do fechamento de folha, um form só para todas as linhas).
                var form = el.form || el.closest('form');
                if (form && (el.tagName === 'BUTTON' || el.type === 'submit')) {
                    if (form.requestSubmit) {
                        form.requestSubmit(el);
                    } else {
                        form.submit();
                    }
                    return;
                }
                if (el.tagName === 'A' && el.href) {
                    window.location = el.href;
                }
            });
        });
    }
    // Modal de motivo: botão com data-motivo abre uma janela pedindo um texto
    // (ex.: rejeitar documento). O texto vai para o input escondido do próprio
    // form -- indicado por data-motivo-campo, padrão "motivo" -- e só então o
    // form é enviado. Sem texto, não envia.
    var modalMotivo = document.getElementById('modal-motivo');
    if (modalMotivo) {
        var motivoTitulo = document.getElementById('modal-motivo-titulo');
        var motivoMensagem = document.getElementById('modal-motivo-mensagem');
        var motivoTexto = document.getElementById('modal-motivo-texto');
        var motivoErro = document.getElementById('modal-motivo-erro');
        var motivoConfirmar = document.getElementById('modal-motivo-confirmar');
        var motivoCancelar = document.getElementById('modal-motivo-cancelar');
        var botaoMotivo = null;

        function fecharModalMotivo() {
            modalMotivo.hidden = true;
            motivoErro.hidden = true;
            botaoMotivo = null;
        }

        motivoCancelar.addEventListener('click', fecharModalMotivo);
        modalMotivo.addEventListener('click', function (evento) {
            if (evento.target === modalMotivo) fecharModalMotivo();
        });
        document.addEventListener('keydown', function (evento) {
            if (!modalMotivo.hidden && evento.key === 'Escape') fecharModalMotivo();
        });

        motivoConfirmar.addEventListener('click', function () {
            if (!botaoMotivo) return;

            var texto = motivoTexto.value.trim();
            if (!texto) {
                motivoErro.hidden = false;
                motivoTexto.focus();
                return;
            }

            var form = botaoMotivo.closest('form');
            var campo = form && form.querySelector(
                '[name="' + (botaoMotivo.dataset.motivoCampo || 'motivo') + '"]'
            );
            if (!form || !campo) return;

            campo.value = texto;
            var botao = botaoMotivo;
            fecharModalMotivo();
            if (form.requestSubmit) {
                form.requestSubmit(botao);
            } else {
                form.submit();
            }
        });

        document.addEventListener('click', function (evento) {
            var el = evento.target.closest('[data-motivo]');
            if (!el) return;

            evento.preventDefault();
            botaoMotivo = el;
            motivoTitulo.textContent = el.dataset.motivoTitulo || 'Motivo';
            motivoMensagem.textContent = el.dataset.motivo;
            motivoTexto.value = '';
            motivoTexto.placeholder = el.dataset.motivoPlaceholder || '';
            motivoErro.hidden = true;
            modalMotivo.hidden = false;
            motivoTexto.focus();
        });
    }

});
