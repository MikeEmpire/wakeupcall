from apps.weather.providers import FakeWeatherProvider


def test_fake_weather_provider_is_deterministic():
    weather = FakeWeatherProvider().get_current_weather("94107")

    assert weather.location == "ZIP 94107"
    assert weather.temperature_f == 72.0
    assert weather.condition == "clear skies"
