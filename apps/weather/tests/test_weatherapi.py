from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
import requests
from django.test import override_settings

from apps.weather.exceptions import (
    WeatherAuthenticationError,
    WeatherConfigurationError,
    WeatherLocationNotFound,
    WeatherMalformedResponse,
    WeatherProviderTimeout,
    WeatherProviderUnavailable,
    WeatherRateLimited,
)
from apps.weather.weatherapi import WeatherApiProvider


@pytest.fixture
def session():
    return Mock()


@pytest.fixture
def provider(session):
    return WeatherApiProvider(
        api_key="test-api-key",
        base_url="https://weather.example/v1/",
        connect_timeout=1.5,
        read_timeout=4.0,
        session=session,
    )


def response(status_code, payload=None, *, json_error=None):
    result = Mock(status_code=status_code)
    if json_error is not None:
        result.json.side_effect = json_error
    else:
        result.json.return_value = payload
    return result


def successful_payload():
    return {
        "location": {"name": "San Francisco", "region": "California"},
        "current": {
            "last_updated_epoch": 1_720_000_000,
            "temp_f": 61.4,
            "condition": {"text": "Partly cloudy"},
        },
    }


def test_maps_successful_response_to_current_weather(provider, session):
    session.get.return_value = response(200, successful_payload())

    weather = provider.get_current_weather("94107")

    assert weather.location == "San Francisco, California"
    assert weather.temperature_f == 61.4
    assert weather.condition == "Partly cloudy"
    assert weather.observed_at == datetime.fromtimestamp(1_720_000_000, tz=UTC)
    session.get.assert_called_once_with(
        "https://weather.example/v1/current.json",
        params={"key": "test-api-key", "q": "94107", "aqi": "no"},
        timeout=(1.5, 4.0),
    )


def test_rejects_missing_api_key():
    with pytest.raises(WeatherConfigurationError, match="WEATHER_API_KEY"):
        WeatherApiProvider(api_key="")


@override_settings(
    WEATHER_API_KEY="settings-api-key",
    WEATHER_API_BASE_URL="https://settings.example/v2",
    WEATHER_API_CONNECT_TIMEOUT=1.0,
    WEATHER_API_READ_TIMEOUT=3.0,
)
def test_builds_provider_from_django_settings():
    provider = WeatherApiProvider.from_settings()

    assert provider.api_key == "settings-api-key"
    assert provider.base_url == "https://settings.example/v2"
    assert provider.timeout == (1.0, 3.0)


def test_maps_unknown_zip_error(provider, session):
    session.get.return_value = response(
        400,
        {"error": {"code": 1006, "message": "No matching location found."}},
    )

    with pytest.raises(WeatherLocationNotFound) as error:
        provider.get_current_weather("00000")

    assert error.value.retryable is False


def test_maps_quota_error_to_rate_limit(provider, session):
    session.get.return_value = response(
        403,
        {"error": {"code": 2007, "message": "Quota exceeded."}},
    )

    with pytest.raises(WeatherRateLimited) as error:
        provider.get_current_weather("94107")

    assert error.value.retryable is True


def test_maps_invalid_credentials(provider, session):
    session.get.return_value = response(
        401,
        {"error": {"code": 2006, "message": "Invalid key."}},
    )

    with pytest.raises(WeatherAuthenticationError):
        provider.get_current_weather("94107")


def test_maps_request_timeout_without_exposing_key(provider, session):
    session.get.side_effect = requests.Timeout("request failed for test-api-key")

    with pytest.raises(WeatherProviderTimeout) as error:
        provider.get_current_weather("94107")

    assert error.value.retryable is True
    assert "test-api-key" not in str(error.value)


@pytest.mark.parametrize(
    "side_effect",
    [requests.ConnectionError("offline"), requests.RequestException("network error")],
)
def test_maps_network_errors_to_unavailable(provider, session, side_effect):
    session.get.side_effect = side_effect

    with pytest.raises(WeatherProviderUnavailable) as error:
        provider.get_current_weather("94107")

    assert error.value.retryable is True


def test_maps_server_error_without_parsing_body(provider, session):
    server_response = response(503, json_error=ValueError("not JSON"))
    session.get.return_value = server_response

    with pytest.raises(WeatherProviderUnavailable):
        provider.get_current_weather("94107")

    server_response.json.assert_not_called()


@pytest.mark.parametrize(
    ("provider_response", "message"),
    [
        (response(200, json_error=ValueError("bad JSON")), "invalid JSON"),
        (response(200, {"location": {}}), "missing required fields"),
        (
            response(
                200,
                {
                    "location": {"name": ""},
                    "current": {
                        "last_updated_epoch": 1_720_000_000,
                        "temp_f": 61,
                        "condition": {"text": "Clear"},
                    },
                },
            ),
            "empty required fields",
        ),
    ],
)
def test_rejects_malformed_provider_responses(
    provider,
    session,
    provider_response,
    message,
):
    session.get.return_value = provider_response

    with pytest.raises(WeatherMalformedResponse, match=message):
        provider.get_current_weather("94107")
