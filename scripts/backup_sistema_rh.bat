@echo off
rem ======================================================================
rem Backup diario do Sistema RH: banco MySQL (mysqldump) + pasta de midia.
rem Agendado como SYSTEM (ver scripts\README ou PRODUCAO.md).
rem A senha do banco NAO fica aqui: vem do arquivo de credenciais abaixo,
rem que fica fora do repositorio e com acesso restrito.
rem ======================================================================
setlocal

set PROJETO=C:\sistema-rh
set DESTINO=C:\backups\sistema-rh
set CREDENCIAIS=C:\backups\credenciais\mysql_backup.cnf
set MYSQLDUMP="C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe"
set DIAS_RETENCAO=14
rem Pasta compartilhada no servidor do AD. Este servidor (SOTR-PON) nao esta
rem no dominio, entao a conexao usa um usuario do dominio dedicado, com
rem permissao so nessa pasta. A senha fica num arquivo de acesso restrito.
set DESTINO_REDE=\\192.168.0.155\BackupSistemaRH$
set USUARIO_REDE=SOTRIGO\svc_backup_rh
set CREDENCIAL_REDE=C:\backups\credenciais\backup_rede.txt
set DIAS_RETENCAO_REDE=30
set LOG=%PROJETO%\logs\backup.log

rem Data no formato AAAA-MM-DD_HHMM, independente da configuracao regional.
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmm"') do set DATA=%%i

if not exist "%DESTINO%\banco" mkdir "%DESTINO%\banco"
if not exist "%DESTINO%\media" mkdir "%DESTINO%\media"

echo ===== %DATA% ===== >> "%LOG%"

rem ---------- 1. Banco ----------
rem --defaults-extra-file precisa ser o PRIMEIRO argumento do mysqldump.
rem --single-transaction: copia consistente sem travar as tabelas (InnoDB).
rem --no-tablespaces: dispensa o privilegio PROCESS, que o usuario do sistema nao tem.
%MYSQLDUMP% --defaults-extra-file="%CREDENCIAIS%" --single-transaction --no-tablespaces --default-character-set=utf8mb4 --result-file="%DESTINO%\banco\sistema_rh_%DATA%.sql" sistema_rh 2>> "%LOG%"
if errorlevel 1 (
    echo ERRO no mysqldump - backup do banco NAO foi feito >> "%LOG%"
    exit /b 1
)

rem Compacta e apaga o .sql solto (o dump em texto e grande e tem dados pessoais).
powershell -NoProfile -Command "Compress-Archive -Path '%DESTINO%\banco\sistema_rh_%DATA%.sql' -DestinationPath '%DESTINO%\banco\sistema_rh_%DATA%.zip' -Force; Remove-Item '%DESTINO%\banco\sistema_rh_%DATA%.sql'" >> "%LOG%" 2>&1
if errorlevel 1 (
    echo ERRO ao compactar o dump >> "%LOG%"
    exit /b 1
)
echo banco: sistema_rh_%DATA%.zip >> "%LOG%"

rem Apaga dumps mais antigos que a retencao.
forfiles /p "%DESTINO%\banco" /m *.zip /d -%DIAS_RETENCAO% /c "cmd /c del @path" >nul 2>&1

rem ---------- 2. Midia ----------
rem /E (e nao /MIR): copia o que e novo ou mudou e NUNCA apaga no destino.
rem Se um arquivo for apagado por engano no sistema, a copia continua aqui.
robocopy "%PROJETO%\media" "%DESTINO%\media" /E /R:2 /W:5 /NP /NFL /NDL /LOG+:"%LOG%"
rem robocopy: codigos de 0 a 7 sao sucesso; 8 ou mais e erro.
if %ERRORLEVEL% GEQ 8 (
    echo ERRO no robocopy da midia >> "%LOG%"
    exit /b 1
)

rem ---------- 3. Copia para o servidor de backup ----------
set /p SENHA_REDE=<"%CREDENCIAL_REDE%"
net use "%DESTINO_REDE%" /user:%USUARIO_REDE% %SENHA_REDE% /persistent:no >> "%LOG%" 2>&1
set RESULTADO_CONEXAO=%ERRORLEVEL%
set SENHA_REDE=
if not %RESULTADO_CONEXAO%==0 (
    echo ERRO ao conectar no servidor de backup - a copia local foi feita >> "%LOG%"
    exit /b 1
)

robocopy "%DESTINO%" "%DESTINO_REDE%" /E /R:3 /W:10 /NP /NFL /NDL /LOG+:"%LOG%"
set RESULTADO_COPIA=%ERRORLEVEL%

rem Retencao no servidor de backup (maior que a local).
powershell -NoProfile -Command "Get-ChildItem '%DESTINO_REDE%\banco\*.zip' | Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-%DIAS_RETENCAO_REDE%) } | Remove-Item" >> "%LOG%" 2>&1

rem Desconecta antes de avaliar o resultado, para nao deixar a conexao aberta.
net use "%DESTINO_REDE%" /delete /y >nul 2>&1

if %RESULTADO_COPIA% GEQ 8 (
    echo ERRO na copia para o servidor de backup - a copia local foi feita >> "%LOG%"
    exit /b 1
)

echo backup concluido >> "%LOG%"
exit /b 0