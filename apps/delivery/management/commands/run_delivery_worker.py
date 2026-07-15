from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.delivery.queue_services import process_queue_batch
from apps.delivery.sqs import SqsDeliveryQueue
from apps.delivery.twilio_sender import TwilioMessageSender
from apps.weather.weatherapi import WeatherApiProvider


class Command(BaseCommand):
    help = "Long-poll SQS and process versioned delivery messages."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once",
            action="store_true",
            help="Perform one long poll and exit.",
        )
        parser.add_argument(
            "--allow-real-delivery",
            action="store_true",
            help="Permit queued non-demo events to reach Twilio.",
        )

    def handle(self, *args, **options):
        once = options["once"]
        allow_real = options["allow_real_delivery"]
        if allow_real and not settings.DELIVERY_REAL_WORKER_ENABLED:
            raise CommandError("Real queue delivery is disabled by configuration.")
        self._validate_settings()

        queue = SqsDeliveryQueue.from_settings()
        weather_provider = WeatherApiProvider.from_settings()
        message_sender = TwilioMessageSender() if allow_real else None

        while True:
            messages = queue.receive(
                max_messages=settings.DELIVERY_QUEUE_RECEIVE_BATCH_SIZE,
                wait_time_seconds=settings.DELIVERY_QUEUE_WAIT_SECONDS,
                visibility_timeout=settings.DELIVERY_QUEUE_VISIBILITY_SECONDS,
            )
            result = process_queue_batch(
                queue=queue,
                messages=messages,
                weather_provider=weather_provider,
                message_sender=message_sender,
                batch_size=settings.DELIVERY_DISPATCH_BATCH_SIZE,
                max_receive_count=settings.DELIVERY_QUEUE_MAX_RECEIVES,
                retry_base_seconds=settings.DELIVERY_QUEUE_RETRY_BASE_SECONDS,
                retry_max_seconds=settings.DELIVERY_QUEUE_RETRY_MAX_SECONDS,
                grace_period=timedelta(
                    minutes=settings.DELIVERY_MISSED_GRACE_MINUTES
                ),
                allow_real=allow_real,
            )
            self.stdout.write(
                "Worker poll: "
                f"received={result.received_count} "
                f"acknowledged={result.acknowledged_count} "
                f"retry={result.retry_count} "
                f"published={result.published_count} "
                f"malformed={result.malformed_count}."
            )
            if once:
                return

    @staticmethod
    def _validate_settings():
        values = {
            "DELIVERY_QUEUE_RECEIVE_BATCH_SIZE": (
                settings.DELIVERY_QUEUE_RECEIVE_BATCH_SIZE,
                1,
                10,
            ),
            "DELIVERY_QUEUE_WAIT_SECONDS": (
                settings.DELIVERY_QUEUE_WAIT_SECONDS,
                0,
                20,
            ),
            "DELIVERY_QUEUE_VISIBILITY_SECONDS": (
                settings.DELIVERY_QUEUE_VISIBILITY_SECONDS,
                1,
                43200,
            ),
            "DELIVERY_QUEUE_MAX_RECEIVES": (
                settings.DELIVERY_QUEUE_MAX_RECEIVES,
                1,
                1000,
            ),
        }
        for name, (value, minimum, maximum) in values.items():
            if not minimum <= value <= maximum:
                raise CommandError(
                    f"{name} must be between {minimum} and {maximum}."
                )
        if not 1 <= settings.DELIVERY_QUEUE_RETRY_BASE_SECONDS:
            raise CommandError("DELIVERY_QUEUE_RETRY_BASE_SECONDS must be positive.")
        if (
            settings.DELIVERY_QUEUE_RETRY_MAX_SECONDS
            < settings.DELIVERY_QUEUE_RETRY_BASE_SECONDS
            or settings.DELIVERY_QUEUE_RETRY_MAX_SECONDS > 43200
        ):
            raise CommandError(
                "DELIVERY_QUEUE_RETRY_MAX_SECONDS must be between the retry base "
                "and 43200."
            )
