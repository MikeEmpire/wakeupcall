from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from django.utils import timezone


@dataclass(frozen=True)
class CurrentWeather:
    location: str
    temperature_f: float
    condition: str
    observed_at: datetime

    def as_snapshot(self):
        return {
            "location": self.location,
            "temperature_f": self.temperature_f,
            "condition": self.condition,
            "observed_at": self.observed_at.isoformat(),
        }


class WeatherProvider(Protocol):
    def get_current_weather(self, zip_code: str) -> CurrentWeather: ...


class FakeWeatherProvider:
    """Deterministic weather for local development and tests."""

    def get_current_weather(self, zip_code: str) -> CurrentWeather:
        return CurrentWeather(
            location=f"ZIP {zip_code}",
            temperature_f=72.0,
            condition="clear skies",
            observed_at=timezone.now(),
        )
