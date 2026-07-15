import logging
from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from apps.delivery.gateways import MessageSender
from apps.delivery.queueing import (
    DELIVER_SCHEDULED_EVENT,
    DISPATCH_DUE_EVENTS,
    DeliveryQueue,
    MalformedQueueMessage,
    QueueEnvelope,
    ReceivedQueueMessage,
)
from apps.delivery.services import (
    DEFAULT_GRACE_PERIOD,
    MAX_BATCH_SIZE,
    QueueDeliveryAction,
    process_queued_delivery,
)
from apps.scheduling.models import ScheduledEvent
from apps.weather.providers import WeatherProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueuePublicationResult:
    selected_count: int
    published_count: int


@dataclass(frozen=True)
class QueueBatchResult:
    received_count: int
    acknowledged_count: int
    retry_count: int
    published_count: int
    malformed_count: int


def publish_due_event_messages(
    *,
    queue: DeliveryQueue,
    now=None,
    batch_size: int,
    include_real: bool = False,
) -> QueuePublicationResult:
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"Batch size must be between 1 and {MAX_BATCH_SIZE}.")
    dispatch_time = now or timezone.now()
    candidates = ScheduledEvent.objects.filter(
        status=ScheduledEvent.Status.SCHEDULED,
        scheduled_for__lte=dispatch_time,
    )
    if not include_real:
        candidates = candidates.filter(is_demo=True)
    event_ids = list(
        candidates.order_by("scheduled_for", "id").values_list("id", flat=True)[
            :batch_size
        ]
    )

    published_count = 0
    for event_id in event_ids:
        queue.publish(QueueEnvelope.deliver_event(event_id))
        published_count += 1
    return QueuePublicationResult(
        selected_count=len(event_ids),
        published_count=published_count,
    )


def process_queue_batch(
    *,
    queue: DeliveryQueue,
    messages: tuple[ReceivedQueueMessage, ...],
    weather_provider: WeatherProvider,
    message_sender: MessageSender | None,
    batch_size: int,
    max_receive_count: int,
    retry_base_seconds: int,
    retry_max_seconds: int,
    grace_period: timedelta = DEFAULT_GRACE_PERIOD,
    allow_real: bool = False,
) -> QueueBatchResult:
    if max_receive_count < 1:
        raise ValueError("Maximum receive count must be positive.")
    if not 1 <= retry_base_seconds <= retry_max_seconds <= 43200:
        raise ValueError(
            "Retry visibility must be positive, ordered, and at most 43200 seconds."
        )
    acknowledged_count = 0
    retry_count = 0
    published_count = 0
    malformed_count = 0

    for message in messages:
        try:
            envelope = QueueEnvelope.from_json(message.body)
        except MalformedQueueMessage:
            malformed_count += 1
            action = QueueDeliveryAction.RETRY
            logger.warning(
                "Malformed queue message retained for redrive: message_id=%s receive_count=%s",
                message.message_id,
                message.receive_count,
            )
        else:
            if envelope.message_type == DISPATCH_DUE_EVENTS:
                result = publish_due_event_messages(
                    queue=queue,
                    batch_size=batch_size,
                    include_real=allow_real,
                )
                published_count += result.published_count
                action = QueueDeliveryAction.ACKNOWLEDGE
            elif envelope.message_type == DELIVER_SCHEDULED_EVENT:
                action = process_queued_delivery(
                    envelope.event_id,
                    receive_count=message.receive_count,
                    max_receive_count=max_receive_count,
                    weather_provider=weather_provider,
                    message_sender=message_sender,
                    grace_period=grace_period,
                    allow_real=allow_real,
                )
            else:
                raise RuntimeError("A validated queue message had an unknown type.")

        if action == QueueDeliveryAction.ACKNOWLEDGE:
            queue.delete(message)
            acknowledged_count += 1
        else:
            visibility_timeout = min(
                retry_base_seconds * (2 ** max(message.receive_count - 1, 0)),
                retry_max_seconds,
            )
            queue.change_visibility(
                message,
                visibility_timeout=visibility_timeout,
            )
            retry_count += 1

    return QueueBatchResult(
        received_count=len(messages),
        acknowledged_count=acknowledged_count,
        retry_count=retry_count,
        published_count=published_count,
        malformed_count=malformed_count,
    )
