from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[2]

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="unsafe-development-only-key")
DEBUG = env.bool("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "apps.accounts.apps.AccountsConfig",
    "apps.scheduling.apps.SchedulingConfig",
    "apps.delivery.apps.DeliveryConfig",
    "apps.weather.apps.WeatherConfig",
]

MIDDLEWARE = [
    "config.middleware.LoadBalancerHealthCheckMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASE_URL = env("DATABASE_URL", default="")
DATABASE_HOST = env("DATABASE_HOST", default="")
DATABASE_NAME = env("DATABASE_NAME", default="")
DATABASE_USER = env("DATABASE_USER", default="")
DATABASE_PASSWORD = env("DATABASE_PASSWORD", default="")
DATABASE_PORT = env.int("DATABASE_PORT", default=5432)

if DATABASE_URL:
    DATABASES = {"default": env.db_url_config(DATABASE_URL)}
elif all((DATABASE_HOST, DATABASE_NAME, DATABASE_USER, DATABASE_PASSWORD)):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "HOST": DATABASE_HOST,
            "NAME": DATABASE_NAME,
            "USER": DATABASE_USER,
            "PASSWORD": DATABASE_PASSWORD,
            "PORT": DATABASE_PORT,
        }
    }
else:
    DATABASES = {
        "default": env.db_url_config(f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "scheduling_web:event-list"
LOGOUT_REDIRECT_URL = "login"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.BasicAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_THROTTLE_RATES": {
        "phone_verification_start": env(
            "PHONE_VERIFICATION_START_RATE", default="3/hour"
        ),
        "phone_verification_check": env(
            "PHONE_VERIFICATION_CHECK_RATE", default="10/hour"
        ),
    },
}
CORS_ALLOWED_ORIGINS = env.list("DJANGO_CORS_ALLOWED_ORIGINS", default=[])

WEATHER_API_KEY = env("WEATHER_API_KEY", default="")
WEATHER_API_BASE_URL = env(
    "WEATHER_API_BASE_URL",
    default="https://api.weatherapi.com/v1",
)
WEATHER_API_CONNECT_TIMEOUT = env.float("WEATHER_API_CONNECT_TIMEOUT", default=2.0)
WEATHER_API_READ_TIMEOUT = env.float("WEATHER_API_READ_TIMEOUT", default=5.0)

TWILIO_ACCOUNT_SID = env("TWILIO_ACCOUNT_SID", default="")
TWILIO_AUTH_TOKEN = env("TWILIO_AUTH_TOKEN", default="")
TWILIO_VERIFY_SERVICE_SID = env("TWILIO_VERIFY_SERVICE_SID", default="")
TWILIO_SMS_FROM_NUMBER = env("TWILIO_SMS_FROM_NUMBER", default="")
TWILIO_SMS_INBOUND_CALLBACK_URL = env(
    "TWILIO_SMS_INBOUND_CALLBACK_URL",
    default="",
)
TWILIO_HTTP_TIMEOUT = env.float("TWILIO_HTTP_TIMEOUT", default=5.0)
TWILIO_SMS_SMOKE_ENABLED = env.bool("TWILIO_SMS_SMOKE_ENABLED", default=False)
TWILIO_SMS_SMOKE_TO_NUMBER = env("TWILIO_SMS_SMOKE_TO_NUMBER", default="")
TWILIO_VOICE_FROM_NUMBER = env("TWILIO_VOICE_FROM_NUMBER", default="")
TWILIO_VOICE_STATUS_CALLBACK_URL = env(
    "TWILIO_VOICE_STATUS_CALLBACK_URL",
    default="",
)
TWILIO_VOICE_ACTION_CALLBACK_URL = env(
    "TWILIO_VOICE_ACTION_CALLBACK_URL",
    default="",
)
TWILIO_VOICE_SMOKE_ENABLED = env.bool("TWILIO_VOICE_SMOKE_ENABLED", default=False)
TWILIO_VOICE_SMOKE_TO_NUMBER = env("TWILIO_VOICE_SMOKE_TO_NUMBER", default="")

DELIVERY_DISPATCH_BATCH_SIZE = env.int("DELIVERY_DISPATCH_BATCH_SIZE", default=25)
DELIVERY_MISSED_GRACE_MINUTES = env.int(
    "DELIVERY_MISSED_GRACE_MINUTES",
    default=15,
)
DELIVERY_REAL_DISPATCH_ENABLED = env.bool(
    "DELIVERY_REAL_DISPATCH_ENABLED",
    default=False,
)
AWS_REGION = env("AWS_REGION", default="us-east-1")
DELIVERY_QUEUE_URL = env("DELIVERY_QUEUE_URL", default="")
DELIVERY_QUEUE_CONNECT_TIMEOUT = env.float(
    "DELIVERY_QUEUE_CONNECT_TIMEOUT",
    default=2.0,
)
DELIVERY_QUEUE_READ_TIMEOUT = env.float("DELIVERY_QUEUE_READ_TIMEOUT", default=25.0)
DELIVERY_QUEUE_RECEIVE_BATCH_SIZE = env.int(
    "DELIVERY_QUEUE_RECEIVE_BATCH_SIZE",
    default=10,
)
DELIVERY_QUEUE_WAIT_SECONDS = env.int("DELIVERY_QUEUE_WAIT_SECONDS", default=20)
DELIVERY_QUEUE_VISIBILITY_SECONDS = env.int(
    "DELIVERY_QUEUE_VISIBILITY_SECONDS",
    default=120,
)
DELIVERY_QUEUE_MAX_RECEIVES = env.int("DELIVERY_QUEUE_MAX_RECEIVES", default=3)
DELIVERY_QUEUE_RETRY_BASE_SECONDS = env.int(
    "DELIVERY_QUEUE_RETRY_BASE_SECONDS",
    default=30,
)
DELIVERY_QUEUE_RETRY_MAX_SECONDS = env.int(
    "DELIVERY_QUEUE_RETRY_MAX_SECONDS",
    default=300,
)
DELIVERY_REAL_WORKER_ENABLED = env.bool(
    "DELIVERY_REAL_WORKER_ENABLED",
    default=False,
)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {
            "format": "{asctime} {levelname} {name}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": env("DJANGO_LOG_LEVEL", default="INFO"),
    },
    "loggers": {
        "twilio": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
