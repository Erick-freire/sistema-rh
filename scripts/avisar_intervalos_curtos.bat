@echo off
rem Aviso diario ao RH e ao gestor de quem tirou menos de 1h de intervalo de
rem almoco no dia anterior, por notificacao push. Chamado pelo Agendador de
rem Tarefas do Windows as 09:00 (depois da coleta das 06:00) -- ver PRODUCAO.md.
rem Log em logs\avisar_intervalos_curtos.log.
setlocal
cd /d "%~dp0.."
if not exist logs mkdir logs
set LOG=logs\avisar_intervalos_curtos.log
set PYTHONIOENCODING=utf-8

rem O ambiente virtual pode se chamar .venv (este servidor) ou venv.
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=venv\Scripts\python.exe

echo ===== %date% %time% ===== >> "%LOG%"
"%PY%" manage.py avisar_intervalos_curtos >> "%LOG%" 2>&1
