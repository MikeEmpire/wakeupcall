from django.core.management.base import BaseCommand, CommandError

from apps.weather.exceptions import WeatherProviderError
from apps.weather.weatherapi import WeatherApiProvider


class Command(BaseCommand):
    help = "Fetch and display normalized current weather for a US ZIP code."

    def add_arguments(self, parser):
        parser.add_argument("zip_code")

    def handle(self, *args, **options):
        zip_code = options["zip_code"]
        if len(zip_code) != 5 or not zip_code.isdigit():
            raise CommandError("ZIP code must contain exactly five digits.")

        try:
            weather = WeatherApiProvider.from_settings().get_current_weather(zip_code)
        except WeatherProviderError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"{weather.location}: {weather.temperature_f:.0f}°F, "
                f"{weather.condition} (observed {weather.observed_at.isoformat()})"
            )
        )
