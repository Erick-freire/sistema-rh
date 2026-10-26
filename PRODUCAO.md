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

Hoje o sistema roda com `python manage.py runserver`, que é só para
desenvolvimento (não faz HTTPS e não aguenta produção). Caminho recomendado:

1. **Servir com waitress** (WSGI de produção, roda nativo no Windows — já
   está no `requirements.txt`):
   ```
   python -m waitress --listen=127.0.0.1:8000 config.wsgi:application
   ```
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
