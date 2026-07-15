from apps.weather.providers import CurrentWeather


def render_weather_announcement(weather: CurrentWeather) -> str:
    return (
        f"Good morning! The current weather for {weather.location} is "
        f"{weather.temperature_f:.0f}°F with {weather.condition}."
    )
