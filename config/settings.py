from pathlib import Path
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent


# Mantém o valor atual como default (não altera comportamento em ambientes
# sem SECRET_KEY no .env) -- em produção, definir um valor novo e forte no
# .env. Atenção: se CHAVE_CIFRA_SEGREDOS estiver vazia no .env, trocar a
# SECRET_KEY invalida a decifragem de senhas já salvas (Equipamento.senha_admin
# em integracao_gov/fields.py) -- ver PRODUCAO.md antes de rotacionar.
SECRET_KEY = config(
    'SECRET_KEY',
    default='django-insecure-q-85**d&ea#**roz^z%=3b(2+j=gibvswhtc9u7x06^=*jaw4q',
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=True, cast=bool)

ALLOWED_HOSTS = config(
    'ALLOWED_HOSTS',
    default='localhost,127.0.0.1,192.168.1.69',
    cast=Csv(),
)

# Domínios com esquema (https://...) autorizados a enviar POST/CSRF -- em
# produção, preencher no .env com o domínio real por trás do proxy, ex.:
# CSRF_TRUSTED_ORIGINS=https://rh.sotrigo.com.br
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())

# Segurança de cookies e transporte — só entram em vigor quando DEBUG=False
# (em produção, defina DEBUG=False no .env para ativar HTTPS/cookies seguros)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True

# Ativar no .env (ATRAS_DE_PROXY=True) só quando o Django estiver atrás de um
# proxy reverso (IIS/nginx) que já termina o TLS com um certificado real e
# repassa o protocolo original pelo header X-Forwarded-Proto -- sem isso, com
# SECURE_SSL_REDIRECT ligado, o Django não reconhece a requisição como HTTPS
# e entra em loop de redirecionamento.
# NUNCA ativar isso com o Django exposto direto (sem proxy na frente):
# qualquer cliente poderia forjar esse header e enganar o redirect de HTTPS.
if config('ATRAS_DE_PROXY', default=False, cast=bool):
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = True


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
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
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
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

LOGIN_URL = 'login'


# Chave de cifra do EncryptedTextField (integracao_gov/fields.py) -- hoje só
# protege Equipamento.senha_admin (app ponto). Vazia = deriva uma chave a
# partir da SECRET_KEY.
CHAVE_CIFRA_SEGREDOS = config('CHAVE_CIFRA_SEGREDOS', default='')


# Secullum Ponto 4 (SQL Server) -- usado só como referência de leitura para
# importar cadastros já existentes (horários etc). O sistema nunca escreve
# nesse banco.

SECULLUM_SERVER = config('SECULLUM_SERVER', default='')
SECULLUM_INSTANCE = config('SECULLUM_INSTANCE', default='')
SECULLUM_DATABASE = config('SECULLUM_DATABASE', default='')
SECULLUM_USER = config('SECULLUM_USER', default='')
SECULLUM_PASSWORD = config('SECULLUM_PASSWORD', default='')


# Senha padrão usada ao gerar acesso em massa/individual para funcionários já
# contratados sem login (ex.: importados do Secullum) — troca obrigatória no
# primeiro login continua ativa via senha_provisoria=True.

SENHA_PADRAO_ACESSO = config('SENHA_PADRAO_ACESSO', default='Sotrigo@123')


# Google reCAPTCHA v2 ("Não sou um robô") na tela de login -- cadastrar o
# site em https://www.google.com/recaptcha/admin e colar as chaves no .env.
# Com RECAPTCHA_SITE_KEY vazia (padrão em dev, sem conta cadastrada ainda), o
# desafio não aparece e não é exigido no login.
RECAPTCHA_SITE_KEY = config('RECAPTCHA_SITE_KEY', default='')
RECAPTCHA_SECRET_KEY = config('RECAPTCHA_SECRET_KEY', default='')
