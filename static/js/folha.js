// Fechamento de folha (folha/templates/folha/fechamento.html).
//
// Cálculo do ponto: pede lotes ao servidor até não sobrar ninguém pendente e
// recarrega a página. Em lotes porque o cálculo de todos leva mais de 1
// minuto, mais do que o proxy de produção espera por uma resposta.
//
// Grade: cada célula é salva sozinha assim que o RH sai dela (ou marca a
// caixinha), e o servidor devolve o valor já formatado. Enter e as setas
// para cima/baixo andam pela coluna, como numa planilha; Esc desfaz o que foi
// digitado.
document.addEventListener('DOMContentLoaded', function () {
    var progresso = document.getElementById('calculo-progresso');
    if (progresso) {
        var barra = progresso.querySelector('[data-progresso-barra]');
        var texto = progresso.querySelector('[data-progresso-texto]');
        var erro = progresso.querySelector('[data-progresso-erro]');
        var total = Number(progresso.dataset.total);

        var mostrar = function (restantes) {
            barra.value = total - restantes;
            texto.textContent = 'Faltam ' + restantes + ' de ' + total + '.';
        };

        var calcularLote = function () {
            fetch(progresso.dataset.url, {
                method: 'POST',
                credentials: 'same-origin',
                headers: { 'X-CSRFToken': progresso.dataset.csrf, 'X-Requested-With': 'XMLHttpRequest' },
            })
                .then(function (resposta) {
                    // Sessão expirada devolve a página de login (HTML), não JSON.
                    return resposta.json().catch(function () { return {}; }).then(function (dados) {
                        return { ok: resposta.ok, dados: dados };
                    });
                })
                .then(function (resultado) {
                    var dados = resultado.dados;
                    if (!resultado.ok || typeof dados.restantes !== 'number') {
                        erro.textContent = (dados.erro || 'O cálculo parou.') + ' Recarregue a página para continuar de onde parou.';
                        erro.hidden = false;
                        return;
                    }
                    mostrar(dados.restantes);
                    if (dados.restantes > 0) {
                        calcularLote();
                    } else {
                        window.location.reload();
                    }
                })
                .catch(function () {
                    erro.textContent = 'O cálculo parou (sem conexão). Recarregue a página para continuar de onde parou.';
                    erro.hidden = false;
                });
        };

        mostrar(Number(progresso.dataset.restantes));
        calcularLote();
    }

    var grade = document.getElementById('grade-folha');
    if (!grade) return;
    var csrf = grade.dataset.csrf;

    function valorDoCampo(input) {
        return input.type === 'checkbox' ? (input.checked ? '1' : '') : input.value;
    }

    grade.querySelectorAll('input[data-campo]').forEach(function (input) {
        input.dataset.salvo = valorDoCampo(input);
    });

    function atualizarAvisos(linha, avisos) {
        var icone = linha.querySelector('[data-avisos]');
        if (!icone) return;
        icone.title = avisos.join(' ');
        icone.hidden = avisos.length === 0;
        if (avisos.length) {
            linha.setAttribute('data-tem-aviso', '');
        } else {
            linha.removeAttribute('data-tem-aviso');
        }
    }

    function mostrarErro(celula, input, mensagem) {
        celula.classList.add('is-erro');
        celula.title = mensagem;
        input.setAttribute('aria-invalid', 'true');
    }

    function salvar(input) {
        var linha = input.closest('tr[data-url]');
        var celula = input.closest('td');
        celula.classList.remove('is-erro', 'is-salvo');
        celula.classList.add('is-salvando');
        input.removeAttribute('aria-invalid');

        fetch(linha.dataset.url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'X-CSRFToken': csrf, 'X-Requested-With': 'XMLHttpRequest' },
            body: new URLSearchParams({ campo: input.dataset.campo, valor: valorDoCampo(input) }),
        })
            .then(function (resposta) {
                if (resposta.redirected) {
                    // Sessão terminou (1 hora sem uso ou 12 horas de login): o servidor mandou para o login.
                    return { ok: false, dados: { erro: 'Sua sessão terminou e o valor não foi salvo. Recarregue a página e entre de novo.' } };
                }
                return resposta.json().then(function (dados) { return { ok: resposta.ok, dados: dados }; });
            })
            .then(function (resultado) {
                celula.classList.remove('is-salvando');
                var dados = resultado.dados;
                if (!resultado.ok) {
                    mostrarErro(celula, input, dados.erro || 'Não foi possível salvar.');
                    return;
                }
                if (input.type === 'checkbox') {
                    input.checked = dados.marcado;
                } else {
                    input.value = dados.texto;
                }
                input.dataset.salvo = valorDoCampo(input);
                celula.classList.toggle('is-editada', dados.editado);
                celula.title = dados.titulo;
                celula.classList.add('is-salvo');
                setTimeout(function () { celula.classList.remove('is-salvo'); }, 1200);
                atualizarAvisos(linha, dados.avisos);
            })
            .catch(function () {
                celula.classList.remove('is-salvando');
                mostrarErro(celula, input, 'Não foi possível salvar. Recarregue a página e tente de novo.');
            });
    }

    grade.addEventListener('change', function (evento) {
        var input = evento.target.closest('input[data-campo]');
        if (input && valorDoCampo(input) !== input.dataset.salvo) salvar(input);
    });

    function linhaVisivel(linha, passo) {
        var proxima = passo > 0 ? linha.nextElementSibling : linha.previousElementSibling;
        while (proxima && proxima.hidden) {
            proxima = passo > 0 ? proxima.nextElementSibling : proxima.previousElementSibling;
        }
        return proxima;
    }

    grade.addEventListener('keydown', function (evento) {
        var input = evento.target.closest('input[type="text"][data-campo]');
        if (!input) return;
        if (evento.key === 'Escape') {
            input.value = input.dataset.salvo;
            return;
        }
        var passo = { Enter: 1, ArrowDown: 1, ArrowUp: -1 }[evento.key];
        if (!passo) return;
        evento.preventDefault();
        var destino = linhaVisivel(input.closest('tr'), evento.shiftKey && evento.key === 'Enter' ? -1 : passo);
        var campo = destino && destino.querySelector('input[data-campo="' + input.dataset.campo + '"]');
        if (campo) {
            campo.focus();
            campo.select();
        } else {
            input.blur();  // última linha: sair do campo já salva
        }
    });

    var busca = document.getElementById('busca-folha');
    var soComAvisos = document.getElementById('so-com-avisos');

    function filtrar() {
        var termo = busca ? busca.value.trim().toLowerCase() : '';
        var avisos = soComAvisos && soComAvisos.checked;
        grade.querySelectorAll('tbody tr[data-url]').forEach(function (linha) {
            var bate = !termo || linha.dataset.busca.indexOf(termo) !== -1;
            linha.hidden = !bate || (avisos && !linha.hasAttribute('data-tem-aviso'));
        });
    }

    if (busca) busca.addEventListener('input', filtrar);
    if (soComAvisos) soComAvisos.addEventListener('change', filtrar);
});
