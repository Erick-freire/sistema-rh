import base64
import sys
from pathlib import Path
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent



SECRET_KEY = config(
    'SECRET_KEY',
    default='django-insecure-q-85**d&ea#**roz^z%=3b(2+j=gibvswhtc9u7x06^=*jaw4q',
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = config(
    'ALLOWED_HOSTS',
    default='localhost,127.0.0.1,',
    cast=Csv(),
)

# Domínios com esquema (https://...) autorizados a enviar POST/CSRF -- em
# produção, preencher no .env com o domínio real por trás do proxy, ex.:
# CSRF_TRUSTED_ORIGINS=https://rh.sotrigo.com.br
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())

ATRAS_DE_PROXY = config('ATRAS_DE_PROXY', default=False, cast=bool)

# Cookies com flag Secure só são aceitos pelo navegador em HTTPS -- em HTTP puro
# (runserver/waitress direto no IP) o cookie de CSRF é descartado e todo login
# falha com 403. Por isso dependem de ATRAS_DE_PROXY (HTTPS de verdade), não de DEBUG.
SESSION_COOKIE_SECURE = ATRAS_DE_PROXY
CSRF_COOKIE_SECURE = ATRAS_DE_PROXY
CSRF_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True

# SECURE_SSL_REDIRECT e HSTS dependem de ATRAS_DE_PROXY (não de DEBUG), porque
# testar DEBUG=False localmente sem um proxy HTTPS de verdade quebra o runserver
# e o navegador passa a forçar HTTPS em localhost via HSTS até ser limpo manualmente.
SECURE_SSL_REDIRECT = ATRAS_DE_PROXY
SECURE_HSTS_SECONDS = 31536000 if ATRAS_DE_PROXY else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = ATRAS_DE_PROXY

# Ativar no .env (ATRAS_DE_PROXY=True) só quando o Django estiver atrás de um
# proxy reverso (IIS/nginx) que já termina o TLS com um certificado real e
# repassa o protocolo original pelo header X-Forwarded-Proto -- sem isso, com
# SECURE_SSL_REDIRECT ligado, o Django não reconhece a requisição como HTTPS
# e entra em loop de redirecionamento.
# NUNCA ativar isso com o Django exposto direto (sem proxy na frente):
# qualquer cliente poderia forjar esse header e enganar o redirect de HTTPS.
if ATRAS_DE_PROXY:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = True

TUNEL_DEV = config('TUNEL_DEV', default=False, cast=bool)

if TUNEL_DEV:
    ALLOWED_HOSTS += ['.trycloudflare.com']
    CSRF_TRUSTED_ORIGINS += ['https://*.trycloudflare.com']
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'funcionarios',
    'integracao_gov',
    'ponto',
    'terceiros',
    'folha',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Serve /static/ com DEBUG=False (o Django sozinho só serve estáticos em DEBUG)
    'whitenoise.middleware.WhiteNoiseMiddleware',
    # Logo depois do WhiteNoise: marca `private` em tudo que não é estático.
    'funcionarios.middleware.SoCacheDoNavegadorMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    # Depois da autenticação: encerra o login com mais de 12 horas.
    'funcionarios.middleware.LimiteDoLoginMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'funcionarios.middleware.SenhaProvisoriaMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'funcionarios.context_processors.usuario_logado',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': config('DB_NAME'),
        'USER': config('DB_USER'),
        'PASSWORD': config('DB_PASSWORD'),
        'HOST': config('DB_HOST'),
        'PORT': config('DB_PORT'),
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'pt-br'

TIME_ZONE = 'America/Sao_Paulo'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
# Serve direto das pastas de origem (static/ e apps), sem precisar rodar
# collectstatic a cada mudança -- basta reiniciar o servidor.
WHITENOISE_USE_FINDERS = True
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

LOGIN_URL = 'login'

# Sessão (decisão de 13/11/2026, igual para toda conta -- RH, gestor,
# colaborador, terceiro e /admin/): sai depois de 1 hora sem uso, e o login vale
# no máximo 12 horas mesmo em uso contínuo. Antes era o padrão do Django
# (2 semanas desde o login).
# - 1 hora sem uso: o próprio Django. SESSION_SAVE_EVERY_REQUEST renova o prazo
#   (no banco e no cookie) a cada requisição, então conta o tempo sem uso.
# - 12 horas: `funcionarios.middleware.LimiteDoLoginMiddleware`.
SESSION_COOKIE_AGE = 60 * 60
SESSION_SAVE_EVERY_REQUEST = True
DURACAO_MAXIMA_DO_LOGIN = 12 * 60 * 60

# Todo mundo entra pelo CPF; gestor também pelo primeiro nome.
AUTHENTICATION_BACKENDS = ['funcionarios.backends.CpfOuLoginGestorBackend']


# Chave de cifra do EncryptedTextField (integracao_gov/fields.py) -- hoje só
# protege Equipamento.senha_admin (app ponto). Vazia = deriva uma chave a
# partir da SECRET_KEY.
CHAVE_CIFRA_SEGREDOS = config('CHAVE_CIFRA_SEGREDOS', default='')

# `manage.py test` força DEBUG=False, e sem chave dedicada o EncryptedTextField
# recusa cifrar (é a proteção de produção). Só nesse comando, e só se nenhuma
# chave foi configurada, usa esta chave de teste: tudo o que ela cifra vai pro
# banco de teste, que é criado e destruído na rodada. Fora do `test` nada muda.
if not CHAVE_CIFRA_SEGREDOS and sys.argv[1:2] == ['test']:
    CHAVE_CIFRA_SEGREDOS = base64.urlsafe_b64encode(b'chave-de-teste-nao-usar-em-prod!').decode()


# Secullum Ponto 4 (SQL Server) -- usado só como referência de leitura para
# importar cadastros já existentes (horários etc). O sistema nunca escreve
# nesse banco.

SECULLUM_SERVER = config('SECULLUM_SERVER', default='')
SECULLUM_INSTANCE = config('SECULLUM_INSTANCE', default='')
SECULLUM_DATABASE = config('SECULLUM_DATABASE', default='')
SECULLUM_USER = config('SECULLUM_USER', default='')
SECULLUM_PASSWORD = config('SECULLUM_PASSWORD', default='')



# Google reCAPTCHA v2 ("Não sou um robô") na tela de login -- cadastrar o
# site em https://www.google.com/recaptcha/admin e colar as chaves no .env.
# Com RECAPTCHA_SITE_KEY vazia (padrão em dev, sem conta cadastrada ainda), o
# desafio não aparece e não é exigido no login.
RECAPTCHA_SITE_KEY = config('RECAPTCHA_SITE_KEY', default='')
RECAPTCHA_SECRET_KEY = config('RECAPTCHA_SECRET_KEY', default='')


# Notificações push do PWA (Web Push com VAPID). Gerar o par de chaves uma
# vez com `manage.py gerar_chaves_vapid` e colar no .env -- trocar as chaves
# depois invalida a inscrição de todos os aparelhos. Com as chaves vazias, o
# push fica desligado: o botão de ativar não aparece e nada é enviado.
VAPID_PUBLIC_KEY = config('VAPID_PUBLIC_KEY', default='')
VAPID_PRIVATE_KEY = config('VAPID_PRIVATE_KEY', default='')
# Contato que os serviços de push (Google, Apple, Mozilla) usam se houver
# algum problema com os envios.
VAPID_CONTATO = config('VAPID_CONTATO', default='mailto:tisuporte@sotrigo.com.br')
# O envio sai numa thread separada para a tela não esperar o Google/Apple
# responderem. Os testes desligam para conferir o envio na hora.
PUSH_EM_SEGUNDO_PLANO = True
