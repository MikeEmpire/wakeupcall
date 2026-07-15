from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.delivery.queue_services import publish_due_event_messages
from apps.delivery.services import MAX_BATCH_SIZE
from apps.delivery.sqs import SqsDeliveryQueue


class Command(BaseCommand):
    help = "Publish one bounded batch of due event identifiers to SQS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size",
            type=int,
            default=settings.DELIVERY_DISPATCH_BATCH_SIZE,
        )
        parser.add_argument(
            "--allow-real-delivery",
            action="store_true",
            help="Include non-demo event identifiers.",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        allow_real = options["allow_real_delivery"]
        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            raise CommandError(
                f"Batch size must be between 1 and {MAX_BATCH_SIZE}."
            )
        if allow_real and not settings.DELIVERY_REAL_WORKER_ENABLED:
            raise CommandError("Real queue delivery is disabled by configuration.")

        result = publish_due_event_messages(
            queue=SqsDeliveryQueue.from_settings(),
            batch_size=batch_size,
            include_real=allow_real,
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Queue publication finished: "
                f"selected={result.selected_count} "
                f"published={result.published_count}."
            )
        )
