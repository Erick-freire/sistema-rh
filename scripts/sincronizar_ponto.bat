@echo off
rem Sincronizacao automatica do ponto (Enviar + Receber de todos os
rem equipamentos), chamada pelo Agendador de Tarefas do Windows as 06:00 e
rem 18:00 -- ver SETUP_PONTO.md, secao 7. Log em logs\sincronizar_ponto.log.
setlocal
cd /d "%~dp0.."
if not exist logs mkdir logs
set LOG=logs\sincronizar_ponto.log
set PYTHONIOENCODING=utf-8

echo ===== %date% %time% ===== >> "%LOG%"
"venv\Scripts\python.exe" manage.py sincronizar_pendencias_control_id >> "%LOG%" 2>&1
"venv\Scripts\python.exe" manage.py coletar_marcacoes_control_id >> "%LOG%" 2>&1
