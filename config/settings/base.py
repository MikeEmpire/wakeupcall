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

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
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

REST_FRAMEWORK = {}
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
TWILIO_HTTP_TIMEOUT = env.float("TWILIO_HTTP_TIMEOUT", default=5.0)
TWILIO_SMS_SMOKE_ENABLED = env.bool("TWILIO_SMS_SMOKE_ENABLED", default=False)
TWILIO_SMS_SMOKE_TO_NUMBER = env("TWILIO_SMS_SMOKE_TO_NUMBER", default="")
TWILIO_VOICE_FROM_NUMBER = env("TWILIO_VOICE_FROM_NUMBER", default="")
TWILIO_VOICE_STATUS_CALLBACK_URL = env(
    "TWILIO_VOICE_STATUS_CALLBACK_URL",
    default="",
)
TWILIO_VOICE_SMOKE_ENABLED = env.bool("TWILIO_VOICE_SMOKE_ENABLED", default=False)
TWILIO_VOICE_SMOKE_TO_NUMBER = env("TWILIO_VOICE_SMOKE_TO_NUMBER", default="")

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
