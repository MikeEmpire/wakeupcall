from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.delivery.exceptions import DeliveryError
from apps.delivery.services import deliver_scheduled_event
from apps.delivery.twilio_voice import TwilioVoiceSender
from apps.scheduling.models import ScheduledEvent
from apps.weather.providers import FakeWeatherProvider


class Command(BaseCommand):
    help = "Submit one authorized, due non-demo voice event to Twilio."

    def add_arguments(self, parser):
        parser.add_argument("event_id", type=int)
        parser.add_argument(
            "--confirm-call",
            action="store_true",
            help="Confirm that this command may place a real Twilio call.",
        )

    def handle(self, *args, **options):
        if not settings.TWILIO_VOICE_SMOKE_ENABLED:
            raise CommandError("Twilio voice staging smoke calls are disabled.")
        if not options["confirm_call"]:
            raise CommandError("Pass --confirm-call to authorize a real voice call.")
        if not settings.TWILIO_VOICE_SMOKE_TO_NUMBER:
            raise CommandError("The staging voice destination is not configured.")

        event_id = options["event_id"]
        try:
            event = ScheduledEvent.objects.select_related("phone_number").get(
                id=event_id
            )
        except ScheduledEvent.DoesNotExist as exc:
            raise CommandError(f"Event {event_id} does not exist.") from exc

        if event.is_demo:
            raise CommandError("Demo events cannot be sent through Twilio.")
        if event.channel != ScheduledEvent.Channel.VOICE:
            raise CommandError("This command only processes voice events.")
        if event.phone_number.number != settings.TWILIO_VOICE_SMOKE_TO_NUMBER:
            raise CommandError(
                "The event destination does not match the authorized voice destination."
            )

        try:
            attempt = deliver_scheduled_event(
                event_id,
                weather_provider=FakeWeatherProvider(),
                message_sender=TwilioVoiceSender.from_settings(),
            )
        except DeliveryError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Event {event_id} finished with status {attempt.status}."
            )
        )
