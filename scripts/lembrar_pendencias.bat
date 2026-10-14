@echo off
rem Lembrete diario de pendencias (documentos, contrato e comodato a
rem assinar) por notificacao push, chamado pelo Agendador de Tarefas do
rem Windows as 09:00 -- ver PRODUCAO.md. Log em logs\lembrar_pendencias.log.
setlocal
cd /d "%~dp0.."
if not exist logs mkdir logs
set LOG=logs\lembrar_pendencias.log
set PYTHONIOENCODING=utf-8

echo ===== %date% %time% ===== >> "%LOG%"
"venv\Scripts\python.exe" manage.py lembrar_pendencias >> "%LOG%" 2>&1
