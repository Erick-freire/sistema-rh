@echo off
rem ===========================================================================
rem  SINCRONIZACAO AUTOMATICA DO PONTO (Control iD)
rem ===========================================================================
rem
rem  QUANDO RODA
rem    Pelo Agendador de Tarefas do Windows, todo dia as 06:00 e as 18:00:
rem      \SistemaRH\Sincronizar ponto 06h
rem      \SistemaRH\Sincronizar ponto 18h
rem    As tarefas rodam como SYSTEM ("/ru SYSTEM"), entao funcionam mesmo sem
rem    ninguem logado no servidor. Comandos de criacao: SETUP_PONTO.md, secao 7.
rem
rem  O QUE FAZ (nesta ordem, o mesmo que o botao "Sincronizar todos" da tela
rem  Ponto > Equipamentos -- o botao continua disponivel, os dois convivem):
rem    1. sincronizar_pendencias_control_id
rem       ENVIA aos relogios so o que esta na fila (SincronizacaoFuncionario
rem       com status "pendente"): cadastro de admitido, nome/CPF alterado,
rem       desativacao de desligado. Fila vazia = nada e enviado.
rem    2. coletar_marcacoes_control_id
rem       RECEBE as batidas novas de cada relogio (so NSR maior que o ultimo
rem       coletado -- nunca duplica) e liga cada batida ao funcionario pelo
rem       identificador do relogio / CPF / PIS. Tambem revincula batidas sem
rem       dono desde o 1o dia do mes anterior.
rem    Relogio offline ou com erro nao trava os outros: o erro fica gravado no
rem    equipamento (tela de Equipamentos) e no log, e a proxima rodada tenta de
rem    novo. Rodar duas vezes seguidas (agendado + botao) nao causa problema.
rem
rem  ONDE VER O RESULTADO
rem    logs\sincronizar_ponto.log (na pasta do projeto). Cada rodada comeca com
rem    "===== data hora =====" e termina com "codigo de saida" de cada comando
rem    (0 = ok). Tela Ponto > Equipamentos mostra a ultima comunicacao.
rem
rem  COMANDOS UTEIS (prompt como administrador)
rem    Rodar agora:   schtasks /run /tn "SistemaRH\Sincronizar ponto 06h"
rem    Ver status:    schtasks /query /tn "SistemaRH\Sincronizar ponto 06h" /v /fo list
rem    Pausar:        schtasks /change /tn "SistemaRH\Sincronizar ponto 06h" /disable
rem    Reativar:      schtasks /change /tn "SistemaRH\Sincronizar ponto 06h" /enable
rem    Remover:       schtasks /delete /tn "SistemaRH\Sincronizar ponto 06h" /f
rem    (idem para "SistemaRH\Sincronizar ponto 18h")
rem
rem  ATENCAO
rem    Em banco novo, rodar reconciliar_funcionarios_equipamento ANTES da
rem    primeira sincronizacao (SETUP_PONTO.md) -- senao a fila manda recadastrar
rem    nos relogios quem ja esta la, duplicando sem biometria.
rem ===========================================================================
setlocal
cd /d "%~dp0.."
if not exist logs mkdir logs
set LOG=logs\sincronizar_ponto.log
set PYTHONIOENCODING=utf-8

rem O ambiente virtual pode se chamar .venv (este servidor) ou venv.
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=venv\Scripts\python.exe
if not exist "%PY%" (
    echo ===== %date% %time% ===== ERRO: python do ambiente virtual nao encontrado >> "%LOG%"
    exit /b 1
)

echo ===== %date% %time% ===== >> "%LOG%"
"%PY%" manage.py sincronizar_pendencias_control_id >> "%LOG%" 2>&1
echo codigo de saida (enviar): %errorlevel% >> "%LOG%"
"%PY%" manage.py coletar_marcacoes_control_id >> "%LOG%" 2>&1
echo codigo de saida (receber): %errorlevel% >> "%LOG%"
