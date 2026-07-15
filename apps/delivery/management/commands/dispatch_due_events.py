from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.delivery.services import MAX_BATCH_SIZE, dispatch_due_events
from apps.delivery.twilio_sender import TwilioMessageSender
from apps.weather.providers import FakeWeatherProvider


class Command(BaseCommand):
    help = "Process one bounded batch of due scheduled events."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=settings.DELIVERY_DISPATCH_BATCH_SIZE,
        )
        parser.add_argument(
            "--grace-minutes",
            type=int,
            default=settings.DELIVERY_MISSED_GRACE_MINUTES,
        )
        parser.add_argument(
            "--allow-real-delivery",
            action="store_true",
            help="Include non-demo events and permit real provider calls.",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        grace_minutes = options["grace_minutes"]
        allow_real = options["allow_real_delivery"]

        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            raise CommandError(
                f"Batch size must be between 1 and {MAX_BATCH_SIZE}."
            )
        if grace_minutes < 0:
            raise CommandError("Grace minutes cannot be negative.")
        if allow_real and not settings.DELIVERY_REAL_DISPATCH_ENABLED:
            raise CommandError(
                "Real dispatcher delivery is disabled by configuration."
            )

        result = dispatch_due_events(
            weather_provider=FakeWeatherProvider(),
            message_sender=TwilioMessageSender() if allow_real else None,
            batch_size=batch_size,
            grace_period=timedelta(minutes=grace_minutes),
            include_real=allow_real,
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Dispatch finished: "
                f"selected={result.selected_count} "
                f"delivered={result.delivered_count} "
                f"missed={result.missed_count} "
                f"failed={result.failed_count}."
            )
        )
