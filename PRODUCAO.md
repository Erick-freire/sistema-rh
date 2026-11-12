# Checklist para subir em produção

## 1. Variáveis de ambiente (.env do servidor)

```
DEBUG=False
SECRET_KEY=<gerar uma nova, forte>          # ver nota na seção 3 antes de trocar
ALLOWED_HOSTS=<domínio ou IP real>
CSRF_TRUSTED_ORIGINS=https://<domínio real>
RECAPTCHA_SITE_KEY=<da conta do reCAPTCHA>
RECAPTCHA_SECRET_KEY=<da conta do reCAPTCHA>
ATRAS_DE_PROXY=True                          # só se houver IIS/nginx com HTTPS na frente (seção 2)
```

Com `DEBUG=False`, os cookies de sessão/CSRF seguros já ligam sozinhos.
O redirect para HTTPS (`SECURE_SSL_REDIRECT`) e o HSTS **não** dependem de
`DEBUG`: só ligam com `ATRAS_DE_PROXY=True`, que deve ficar `True` apenas
quando o Nginx/IIS com certificado real estiver na frente do sistema. Assim
dá para testar `DEBUG=False` localmente com o runserver em HTTP sem o
navegador passar a forçar HTTPS em localhost — ver `config/settings.py`.

## 2. Certificado SSL / HTTPS

Ainda sem HTTPS. Caminho recomendado:

1. **Servir com waitress** (WSGI de produção, roda nativo no Windows) --
   **feito**: roda como o serviço Windows `SistemaRH`, ver
   [Servidor web (serviço SistemaRH)](#servidor-web-serviço-sistemarh) abaixo.
   Não usar `runserver` em produção.
2. **Proxy reverso na frente** terminando o TLS com um certificado real
   (interno da empresa ou Let's Encrypt) e repassando para o waitress em
   HTTP dentro da rede interna — IIS (com ARR + URL Rewrite) ou nginx,
   conforme o que já for usado para hospedar outros sistemas internos.
3. Ligar `ATRAS_DE_PROXY=True` no `.env` do servidor depois que o proxy
   estiver de pé, para o Django confiar no header `X-Forwarded-Proto` e não
   entrar em loop de redirecionamento. **Não ligar isso sem o proxy
   configurado** — com o Django exposto direto, qualquer cliente forja esse
   header e engana o redirect de HTTPS.

Instalar e configurar o proxy/certificado em si é trabalho de infra do
servidor (fora deste repositório). Para só validar o fluxo em HTTPS
localmente antes disso (com aviso de "não confiável" no navegador, mesmo
padrão já usado nos relógios de ponto), dá pra rodar via
`django-extensions` + `runserver_plus` com certificado autoassinado — mas
isso é só para teste, não serve para usuários reais.

## 3. Chave de cifra (senha dos relógios de ponto)

`Equipamento.senha_admin` (app `ponto`) é cifrada com uma chave derivada da
`SECRET_KEY` quando `CHAVE_CIFRA_SEGREDOS` está vazia no `.env`
(`integracao_gov/fields.py`). **Antes de trocar a `SECRET_KEY`**, gerar e
definir `CHAVE_CIFRA_SEGREDOS` explicitamente:

```
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Trocar a `SECRET_KEY` sem isso torna as senhas dos relógios já salvas
indecifráveis (a leitura passa a devolver o texto cifrado bruto ao invés de
levantar erro — ver `EncryptedTextField.to_python`).

## 4. reCAPTCHA

1. Cadastrar o site em https://www.google.com/recaptcha/admin (reCAPTCHA
   v2, "Não sou um robô").
2. Colar `RECAPTCHA_SITE_KEY` e `RECAPTCHA_SECRET_KEY` no `.env` do
   servidor.
3. Sem as chaves (padrão em dev), o desafio simplesmente não aparece — dá
   para subir em produção antes de ter a conta pronta e ligar depois, sem
   deploy extra.

## 5. Notificações push (PWA)

1. `pip install -r requirements.txt` (traz o `pywebpush`) e
   `python manage.py migrate`.
2. Gerar as chaves **uma vez só** e colar a saída no `.env`:

   ```
   python manage.py gerar_chaves_vapid
   ```

   Trocar as chaves depois desliga a inscrição de todos os aparelhos (cada
   um volta a se inscrever sozinho na próxima vez que abrir o sistema, mas
   até lá não recebe nada). Opcional: `VAPID_CONTATO=mailto:...` (padrão
   `tisuporte@sotrigo.com.br`).
3. Sem as chaves, o push fica desligado: o convite e o botão em "Meu Perfil"
   não aparecem.
4. Push exige HTTPS (item 2). No iPhone, só funciona com o app instalado na
   Tela de Início (iOS 16.4 ou mais novo).
5. O servidor precisa conseguir sair para a internet em HTTPS para
   `fcm.googleapis.com`, `*.push.apple.com`, `*.push.services.mozilla.com` e
   `*.notify.windows.com`.
6. Lembrete diário de pendências (documento a enviar, contrato ou comodato a
   assinar), às 09:00:

   ```
   schtasks /create /tn "SistemaRH\Lembrar pendencias 09h" /sc daily /st 09:00 /ru SYSTEM /rl HIGHEST /tr "\"C:\caminho\sistema-rh\scripts\lembrar_pendencias.bat\""
   ```

   Log em `logs\lembrar_pendencias.log`.
7. Aviso diário ao RH e ao gestor de quem tirou menos intervalo que o mínimo
   no dia anterior (1h acima de 6h trabalhadas, 15 min acima de 4h até 6h), às
   09:00 (depois da coleta das 06:00, que traz a saída dos turnos noturnos):

   ```
   schtasks /create /tn "SistemaRH\Avisar intervalos curtos 09h" /sc daily /st 09:00 /ru SYSTEM /rl HIGHEST /tr "\"C:\caminho\sistema-rh\scripts\avisar_intervalos_curtos.bat\""
   ```

   Log em `logs\avisar_intervalos_curtos.log`. Para conferir os casos de um
   dia sem mandar nada: `manage.py avisar_intervalos_curtos --data AAAA-MM-DD --dry-run`.

## Servidor web (serviço SistemaRH)

O sistema roda como serviço do Windows criado com o NSSM (o mesmo usado no
`SistemaCatraca`), e **não depende de ninguém logado nem de janela aberta**:

| Item | Valor |
|---|---|
| Serviço | `SistemaRH` ("Sistema RH (servidor web)") |
| Comando | `C:\sistema-rh\.venv\Scripts\python.exe -m waitress --listen=0.0.0.0:8002 config.wsgi:application` |
| Pasta de trabalho | `C:\sistema-rh` (é de onde vem o `.env`) |
| Conta | LocalSystem |
| Início | Automático (sobe junto com o Windows) |
| Se cair | o NSSM reinicia sozinho em 5 segundos |
| Log | `logs\servidor_web.log` (gira a cada 10 MB) |

Comandos (prompt como administrador):

```
sc query SistemaRH                       :: está rodando?
net stop SistemaRH && net start SistemaRH :: reiniciar (ex.: depois de um git pull)
C:\nssm\win64\nssm.exe edit SistemaRH     :: abrir a configuração na tela
```

**Depois de atualizar o código** (`git pull`, `migrate`, `collectstatic`),
reiniciar o serviço -- o waitress não recarrega sozinho. Não suba o sistema
de novo com `runserver` ou numa janela de PowerShell: a porta 8002 já é do
serviço, e a janela morre quando a sessão do RDP é encerrada.

## Backup (banco + mídia)

Todo dia às 22:00 o script `scripts\backup_sistema_rh.bat` faz o backup do
banco MySQL e da pasta de mídia (documentos, contratos, comodatos, fotos) e
envia uma cópia para o servidor do AD. Roda como `SYSTEM`, **sem ninguém
logado**.

| Item | Valor |
|---|---|
| Tarefa agendada | `SistemaRH\Backup diario 22h` (diária, 22:00, conta `SYSTEM`) |
| Script | `C:\sistema-rh\scripts\backup_sistema_rh.bat` |
| Log | `C:\sistema-rh\logs\backup.log` (última linha de uma execução boa: `backup concluido`) |
| Cópia local | `C:\backups\sistema-rh\banco` (dumps `.zip`) e `C:\backups\sistema-rh\media` |
| Cópia remota | `\\192.168.0.155\BackupSistemaRH$` (servidor do AD, `STR-SRV-01`, pasta `C:\BackupSistemaRH`) |
| Retenção do banco | 14 dias na cópia local, 30 dias na remota |
| Retenção da mídia | sem limpeza: a cópia só acrescenta e atualiza, nunca apaga |

### O que o script faz, em ordem

1. **Banco:** `mysqldump` com `--single-transaction` (cópia consistente sem
   travar o sistema, o RH pode estar usando), compacta em
   `sistema_rh_AAAA-MM-DD_HHMM.zip` e apaga o `.sql` solto.
2. **Mídia:** `robocopy /E` de `C:\sistema-rh\media` para a cópia local.
   Usa `/E` e não `/MIR` de propósito: um arquivo apagado por engano no
   sistema continua no backup.
3. **Envio:** conecta no compartilhamento do AD com o usuário
   `SOTRIGO\svc_backup_rh`, copia a pasta local inteira, aplica a retenção
   remota e desconecta.

Qualquer falha grava uma linha com `ERRO` no log e encerra com código 1. Se
só o envio falhar, a cópia local daquele dia foi feita mesmo assim.

### Credenciais (nenhuma senha fica no script nem no Git)

Ficam em `C:\backups\credenciais`, pasta acessível **só** para
Administradores e `SYSTEM` (herança removida com `icacls`):

| Arquivo | Conteúdo | Usado por |
|---|---|---|
| `mysql_backup.cnf` | `[client]` com `user`, `password` e `host` do usuário `sistema_rh` | `mysqldump` |
| `backup_rede.txt` | só a senha do `svc_backup_rh`, uma linha, sem espaços | `net use` do script |

Salvar esses arquivos pelo VS Code (UTF-8 **sem** BOM). O Bloco de Notas
pode gravar o BOM, que vira parte da senha e faz a conexão falhar.

**Trocou alguma senha? Atualize os dois lados no mesmo momento:**

- Senha do MySQL `sistema_rh`: `ALTER USER` no MySQL + `DB_PASSWORD` no
  `.env` + `mysql_backup.cnf` (e reiniciar o serviço `SistemaRH`).
- Senha do `svc_backup_rh`: `Set-ADAccountPassword` no servidor do AD +
  `backup_rede.txt`.

### Usuário `svc_backup_rh` e a pasta no AD

Este servidor (`SOTR-PON`) **não está no domínio** (WORKGROUP), por isso a
conexão usa um usuário do domínio dedicado em vez da conta do computador.

- Usuário `SOTRIGO\svc_backup_rh`: senha que não expira e que ele não pode
  trocar (se expirasse, o backup pararia em silêncio). Não usar essa conta
  para mais nada.
- Compartilhamento oculto `BackupSistemaRH$`: `svc_backup_rh` com
  Alteração; acesso total para o administrador.
- NTFS de `C:\BackupSistemaRH`: `svc_backup_rh` com Modificar,
  Administradores e `SYSTEM` com Controle total, **sem herança**. A pasta
  fica fora de `C:\Compartilhamentos` de propósito: o backup tem dados
  pessoais de todos os funcionários e não pode herdar o acesso dos usuários.
- Atenção ao espaço livre no `C:` do servidor do AD: a mídia só cresce.

### Rodar e conferir manualmente

```
schtasks /run /tn "SistemaRH\Backup diario 22h"
schtasks /query /tn "SistemaRH\Backup diario 22h" /v /fo list | Select-String "Status", "Resultado"
Get-Content C:\sistema-rh\logs\backup.log -Tail 10
```

Testar sempre pela tarefa (`schtasks /run`), e não rodando o `.bat` na
própria sessão: assim ele roda como `SYSTEM`, igual à execução das 22:00.

Para testar só o acesso ao compartilhamento, com o usuário do backup (a
senha é pedida na tela e não vai para o histórico):

```
net use \\192.168.0.155\BackupSistemaRH$ /user:SOTRIGO\svc_backup_rh
net use \\192.168.0.155\BackupSistemaRH$ /delete
```

### Restaurar

**Antes de restaurar em produção, restaure num banco de teste e confira.**

O dump é de um banco só (sem `--databases`), então ele **não** tem
`CREATE DATABASE` nem `USE`: restaura no banco que estiver selecionado.
Confira isso antes de qualquer restauração (não deve aparecer nada):

```
Select-String -Path C:\caminho\sistema_rh_AAAA-MM-DD_HHMM.sql -Pattern '^USE |^CREATE DATABASE'
```

**Banco de teste** (MySQL com o usuário administrador):

```sql
CREATE DATABASE teste_restauracao CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE teste_restauracao;
SOURCE C:/caminho/sistema_rh_AAAA-MM-DD_HHMM.sql;
-- conferir e depois:
DROP DATABASE teste_restauracao;
```

No `SOURCE`, usar barras normais (`/`), mesmo no Windows.

**Produção** (só em caso de perda real de dados):

1. Parar o sistema, para ninguém gravar durante a restauração:
   `Stop-Service SistemaRH`.
2. Guardar o estado atual antes de sobrescrever, rodando a tarefa de backup
   (`schtasks /run ...`) e conferindo o log.
3. No MySQL, com o administrador:
   ```sql
   DROP DATABASE sistema_rh;
   CREATE DATABASE sistema_rh CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   USE sistema_rh;
   SOURCE C:/caminho/sistema_rh_AAAA-MM-DD_HHMM.sql;
   ```
   As permissões do usuário `sistema_rh` continuam valendo: no MySQL elas
   são dadas pelo nome do banco, não pelo banco físico.
4. Mídia, se necessário: copiar de volta com
   `robocopy C:\backups\sistema-rh\media C:\sistema-rh\media /E`.
5. `Start-Service SistemaRH` e testar login e uma ficha.

Os `.sql` extraídos têm dados pessoais de todos os funcionários: extrair
dentro de `C:\backups` (pasta protegida) e apagar logo depois de usar.

## Acesso externo (Cloudflare Tunnel)

O sistema é acessado **só** por `https://rh.sotrix.com.br`, de dentro ou de
fora da empresa. O endereço antigo `http://10.4.23.6:8002` não funciona
mais (de propósito).

| Item | Valor |
|---|---|
| Endereço | `https://rh.sotrix.com.br` |
| Túnel | o túnel `catraca` da Cloudflare (Zero Trust → Networks → Tunnels), que é o túnel **deste servidor** (`SOTR-PON`) |
| Rota no túnel | `rh.sotrix.com.br` → `HTTP` `localhost:8002` |
| Conector | serviço do Windows `cloudflared`, token em `C:\ProgramData\cloudflared\token` |
| HTTPS | entregue pela Cloudflare; entre o `cloudflared` e o Waitress é HTTP local |
| Firewall | nenhuma porta aberta para o sistema (a regra da 8002 foi removida) |

**O túnel `catraca` também leva o sistema de RH.** Não apague o túnel nem
troque o token achando que é "só da catraca": os dois sistemas caem juntos.
No painel, ao mexer no túnel, só **acrescente** ou edite a rota do sistema
que você quer; não altere a rota da catraca.

### Como o caminho funciona

```
navegador --HTTPS--> Cloudflare --túnel--> cloudflared (este servidor) --HTTP--> Waitress 127.0.0.1:8002 --> Django
```

O Waitress do serviço `SistemaRH` roda com:

```
-m waitress --listen=127.0.0.1:8002 --trusted-proxy=127.0.0.1 --trusted-proxy-headers="x-forwarded-for x-forwarded-proto" config.wsgi:application
```

- `--listen=127.0.0.1`: só o próprio servidor (o `cloudflared`) alcança o
  Waitress. Ninguém da rede consegue falar direto com ele e forjar
  cabeçalhos.
- `--trusted-proxy` / `--trusted-proxy-headers`: o Waitress confia nos
  cabeçalhos que o `cloudflared` envia. Sem isso, ele descarta o
  `X-Forwarded-Proto: https`, o Django acha que tudo é HTTP e o
  `SECURE_SSL_REDIRECT` entra em **loop de redirecionamento** (no Firefox:
  "A página não está sendo redirecionada corretamente").

Para mudar esses argumentos, use a tela do NSSM
(`C:\nssm\win64\nssm.exe edit SistemaRH`, aba Application, campo
Arguments) e clique em **Edit service** — fechar pelo X descarta a
alteração. Confira com `nssm get SistemaRH AppParameters` e reinicie o
serviço.

### `.env` de produção (parte do acesso)

```
ALLOWED_HOSTS=rh.sotrix.com.br,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://rh.sotrix.com.br
ATRAS_DE_PROXY=True
RECAPTCHA_SITE_KEY=...
RECAPTCHA_SECRET_KEY=...
```

`ATRAS_DE_PROXY=True` liga juntos: cookies seguros, redirecionamento para
HTTPS, HSTS e a leitura do `X-Forwarded-Proto`. **Só pode ficar `True`
com o Waitress em `127.0.0.1`.** Esses valores vão no `.env`, nunca no
`config/settings.py` (código, que só muda por `git pull`).

### Diagnóstico rápido

Simular a Cloudflare no próprio servidor:

```
curl.exe -s -o NUL -w "%{http_code} %{redirect_url}\n" -H "Host: rh.sotrix.com.br" -H "X-Forwarded-Proto: https" http://127.0.0.1:8002/
```

- `302` para a tela de login (ou `200`): Django e Waitress certos; se o
  site não abre, o problema está na Cloudflare ou no `cloudflared`.
- `301` para `https://rh.sotrix.com.br/`: o Waitress está descartando o
  cabeçalho; conferir os argumentos do serviço.
- Erro de conexão: o serviço `SistemaRH` não está rodando (ver
  `logs\servidor_web.log`).

O navegador memoriza redirecionamentos 301: depois de corrigir um loop,
testar numa janela privativa.

### Voltar para o acesso só pela rede interna (emergência)

1. `.env`: `ATRAS_DE_PROXY=False` e `10.4.23.6` de volta no `ALLOWED_HOSTS`.
2. Argumentos do serviço: `-m waitress --listen=0.0.0.0:8002 config.wsgi:application`.
3. Firewall:
   `New-NetFirewallRule -DisplayName "Sistema RH (8002)" -Direction Inbound -Protocol TCP -LocalPort 8002 -RemoteAddress 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16 -Action Allow`
4. `Restart-Service SistemaRH`; acesso por `http://10.4.23.6:8002`.

Nunca deixe `ATRAS_DE_PROXY=True` com o Waitress em `0.0.0.0`.