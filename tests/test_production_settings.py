import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_production_database(extra_environment):
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
        "DJANGO_SECRET_KEY": "test-only-secret-key",
        "DJANGO_ALLOWED_HOSTS": "example.test",
        "DATABASE_URL": "",
        "DATABASE_HOST": "",
        "DATABASE_NAME": "",
        "DATABASE_USER": "",
        "DATABASE_PASSWORD": "",
        **extra_environment,
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from django.conf import settings; "
                "print(json.dumps(settings.DATABASES['default']))"
            ),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    return result


def test_production_accepts_discrete_database_settings():
    result = _load_production_database(
        {
            "DATABASE_HOST": "database.internal",
            "DATABASE_NAME": "wakeupcall",
            "DATABASE_USER": "application",
            "DATABASE_PASSWORD": "not-a-real-password",
            "DATABASE_PORT": "5433",
        }
    )

    assert result.returncode == 0, result.stderr
    database = json.loads(result.stdout)
    assert database["ENGINE"] == "django.db.backends.postgresql"
    assert database["HOST"] == "database.internal"
    assert database["NAME"] == "wakeupcall"
    assert database["USER"] == "application"
    assert database["PORT"] == 5433


def test_production_rejects_incomplete_database_settings():
    result = _load_production_database({"DATABASE_HOST": "database.internal"})

    assert result.returncode != 0
    assert "Production requires DATABASE_URL or all of" in result.stderr


def test_production_static_assets_use_whitenoise():
    environment = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
        "DJANGO_SECRET_KEY": "test-only-secret-key",
        "DJANGO_ALLOWED_HOSTS": "example.test",
        "DATABASE_URL": "sqlite:///:memory:",
    }
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; from django.conf import settings; "
                "print(json.dumps({'middleware': settings.MIDDLEWARE, "
                "'static_backend': settings.STORAGES['staticfiles']['BACKEND']}))"
            ),
        ],
        cwd=ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    configuration = json.loads(result.stdout)
    security_index = configuration["middleware"].index(
        "django.middleware.security.SecurityMiddleware"
    )
    assert configuration["middleware"][security_index + 1] == (
        "whitenoise.middleware.WhiteNoiseMiddleware"
    )
    assert configuration["static_backend"] == (
        "whitenoise.storage.CompressedManifestStaticFilesStorage"
    )


def test_container_build_collects_static_assets():
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "RUN python manage.py collectstatic --noinput" in dockerfile
