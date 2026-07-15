from django.core.management.base import BaseCommand, CommandError

from apps.delivery.services import deliver_scheduled_event
from apps.scheduling.models import ScheduledEvent
from apps.weather.providers import FakeWeatherProvider


class Command(BaseCommand):
    help = "Deliver one due demo event using deterministic fake weather."

    def add_arguments(self, parser):
        parser.add_argument("event_id", type=int)

    def handle(self, *args, **options):
        event_id = options["event_id"]
        try:
            event = ScheduledEvent.objects.get(id=event_id)
        except ScheduledEvent.DoesNotExist as exc:
            raise CommandError(f"Event {event_id} does not exist.") from exc

        if not event.is_demo:
            raise CommandError("This command only processes demo events.")

        attempt = deliver_scheduled_event(
            event_id,
            weather_provider=FakeWeatherProvider(),
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Event {event_id} finished with status {attempt.status}."
            )
        )
