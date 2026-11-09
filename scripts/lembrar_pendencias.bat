@echo off
rem Lembrete diario de pendencias (documentos, contrato e comodato a
rem assinar) por notificacao push, chamado pelo Agendador de Tarefas do
rem Windows as 09:00 -- ver PRODUCAO.md. Log em logs\lembrar_pendencias.log.
setlocal
cd /d "%~dp0.."
if not exist logs mkdir logs
set LOG=logs\lembrar_pendencias.log
set PYTHONIOENCODING=utf-8

rem O ambiente virtual pode se chamar .venv (este servidor) ou venv.
set PY=.venv\Scripts\python.exe
if not exist "%PY%" set PY=venv\Scripts\python.exe

echo ===== %date% %time% ===== >> "%LOG%"
"%PY%" manage.py lembrar_pendencias >> "%LOG%" 2>&1
