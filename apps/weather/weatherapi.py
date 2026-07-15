from datetime import UTC, datetime

import requests
from django.conf import settings

from apps.weather.exceptions import (
    WeatherAuthenticationError,
    WeatherConfigurationError,
    WeatherLocationNotFound,
    WeatherMalformedResponse,
    WeatherProviderTimeout,
    WeatherProviderUnavailable,
    WeatherRateLimited,
)
from apps.weather.providers import CurrentWeather


class WeatherApiProvider:
    """WeatherAPI.com adapter returning project-owned weather data."""

    CURRENT_PATH = "/current.json"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.weatherapi.com/v1",
        connect_timeout: float = 2.0,
        read_timeout: float = 5.0,
        session=None,
    ):
        if not api_key:
            raise WeatherConfigurationError("WEATHER_API_KEY is required.")
        if connect_timeout <= 0 or read_timeout <= 0:
            raise WeatherConfigurationError("Weather API timeouts must be positive.")

        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = (connect_timeout, read_timeout)
        self.session = session or requests.Session()

    @classmethod
    def from_settings(cls):
        return cls(
            api_key=settings.WEATHER_API_KEY,
            base_url=settings.WEATHER_API_BASE_URL,
            connect_timeout=settings.WEATHER_API_CONNECT_TIMEOUT,
            read_timeout=settings.WEATHER_API_READ_TIMEOUT,
        )

    def get_current_weather(self, zip_code: str) -> CurrentWeather:
        try:
            response = self.session.get(
                f"{self.base_url}{self.CURRENT_PATH}",
                params={"key": self.api_key, "q": zip_code, "aqi": "no"},
                timeout=self.timeout,
            )
        except requests.Timeout:
            raise WeatherProviderTimeout("The weather provider timed out.") from None
        except requests.RequestException:
            raise WeatherProviderUnavailable(
                "The weather provider could not be reached."
            ) from None

        if response.status_code >= 500:
            raise WeatherProviderUnavailable(
                "The weather provider is temporarily unavailable."
            )
        if response.status_code == 429:
            raise WeatherRateLimited("The weather provider rate limit was reached.")

        payload = self._parse_json(response)
        if response.status_code >= 400:
            self._raise_api_error(response.status_code, payload)

        return self._map_weather(payload)

    @staticmethod
    def _parse_json(response) -> dict:
        try:
            payload = response.json()
        except ValueError:
            raise WeatherMalformedResponse(
                "The weather provider returned invalid JSON."
            ) from None

        if not isinstance(payload, dict):
            raise WeatherMalformedResponse(
                "The weather provider returned an unexpected response."
            )
        return payload

    @staticmethod
    def _raise_api_error(status_code: int, payload: dict):
        error = payload.get("error")
        error_code = error.get("code") if isinstance(error, dict) else None

        if error_code == 1006:
            raise WeatherLocationNotFound("No weather location matched the ZIP code.")
        if error_code == 2007:
            raise WeatherRateLimited("The weather provider rate limit was reached.")
        if error_code in {1002, 2006, 2008, 2009} or status_code == 401:
            raise WeatherAuthenticationError(
                "The weather provider credentials or access are invalid."
            )
        raise WeatherProviderUnavailable(
            "The weather provider rejected the request."
        )

    @staticmethod
    def _map_weather(payload: dict) -> CurrentWeather:
        try:
            location = payload["location"]
            current = payload["current"]
            name = str(location["name"]).strip()
            region = str(location.get("region", "")).strip()
            condition = str(current["condition"]["text"]).strip()
            temperature_f = float(current["temp_f"])
            observed_at = datetime.fromtimestamp(
                int(current["last_updated_epoch"]),
                tz=UTC,
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            raise WeatherMalformedResponse(
                "The weather provider response was missing required fields."
            ) from None

        if not name or not condition:
            raise WeatherMalformedResponse(
                "The weather provider response contained empty required fields."
            )

        display_location = f"{name}, {region}" if region else name
        return CurrentWeather(
            location=display_location,
            temperature_f=temperature_f,
            condition=condition,
            observed_at=observed_at,
        )
