from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent


SECRET_KEY = 'django-insecure-q-85**d&ea#**roz^z%=3b(2+j=gibvswhtc9u7x06^=*jaw4q'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = config('DEBUG', default=True, cast=bool)

ALLOWED_HOSTS = ['localhost','127.0.0.1','192.168.1.88']

# Segurança de cookies e transporte — só entram em vigor quando DEBUG=False
# (em produção, defina DEBUG=False no .env para ativar HTTPS/cookies seguros)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_HTTPONLY = True
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True


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


# Integração Assinatura Gov.br (Conecta gov.br + Assina gov.br)
# Credenciais reais ainda não emitidas — valores default abaixo são placeholders de sandbox
# e precisam ser confirmados na documentação oficial (ver especificação, seção 8).

GOVBR_CLIENT_ID = config('GOVBR_CLIENT_ID', default='')
GOVBR_CLIENT_SECRET = config('GOVBR_CLIENT_SECRET', default='')
GOVBR_REDIRECT_URI = config('GOVBR_REDIRECT_URI', default='http://localhost:8000/integracao-gov/callback/')
GOVBR_AMBIENTE = config('GOVBR_AMBIENTE', default='sandbox')
GOVBR_AUTHORIZE_URL = config('GOVBR_AUTHORIZE_URL', default='https://sso.staging.acesso.gov.br/authorize')
GOVBR_TOKEN_URL = config('GOVBR_TOKEN_URL', default='https://sso.staging.acesso.gov.br/token')
GOVBR_ASSINATURA_BASE_URL = config('GOVBR_ASSINATURA_BASE_URL', default='https://assinatura-api.staging.iti.gov.br')
GOVBR_WEBHOOK_SECRET = config('GOVBR_WEBHOOK_SECRET', default='')
GOVBR_TOKEN_ENCRYPTION_KEY = config('GOVBR_TOKEN_ENCRYPTION_KEY', default='')


# Secullum Ponto 4 (SQL Server) -- usado só como referência de leitura para
# importar cadastros já existentes (horários etc). O sistema nunca escreve
# nesse banco.

SECULLUM_SERVER = config('SECULLUM_SERVER', default='')
SECULLUM_INSTANCE = config('SECULLUM_INSTANCE', default='')
SECULLUM_DATABASE = config('SECULLUM_DATABASE', default='')
SECULLUM_USER = config('SECULLUM_USER', default='')
SECULLUM_PASSWORD = config('SECULLUM_PASSWORD', default='')
