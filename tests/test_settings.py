from django.conf import settings


def test_development_settings_are_loaded():
    assert settings.SETTINGS_MODULE == "config.settings.development"
    assert settings.TIME_ZONE == "UTC"
    assert settings.USE_TZ is True
    assert settings.AUTH_USER_MODEL == "accounts.User"
