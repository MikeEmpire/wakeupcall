import json
from dataclasses import dataclass
from typing import Protocol

ENVELOPE_VERSION = 1
DISPATCH_DUE_EVENTS = "dispatch_due_events"
DELIVER_SCHEDULED_EVENT = "deliver_scheduled_event"


class QueueError(RuntimeError):
    pass


class QueueConfigurationError(QueueError):
    pass


class QueueUnavailable(QueueError):
    pass


class MalformedQueueMessage(QueueError):
    pass


@dataclass(frozen=True)
class QueueEnvelope:
    message_type: str
    event_id: int | None = None
    version: int = ENVELOPE_VERSION

    @classmethod
    def dispatch_tick(cls):
        return cls(message_type=DISPATCH_DUE_EVENTS)

    @classmethod
    def deliver_event(cls, event_id: int):
        if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id <= 0:
            raise ValueError("The event identifier must be a positive integer.")
        return cls(message_type=DELIVER_SCHEDULED_EVENT, event_id=event_id)

    def to_json(self) -> str:
        payload = {"version": self.version, "type": self.message_type}
        if self.event_id is not None:
            payload["event_id"] = self.event_id
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, body: str):
        if not isinstance(body, str) or len(body) > 4096:
            raise MalformedQueueMessage("The queue message body is invalid.")
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            raise MalformedQueueMessage("The queue message is not valid JSON.") from None
        if not isinstance(payload, dict) or payload.get("version") != ENVELOPE_VERSION:
            raise MalformedQueueMessage("The queue message version is unsupported.")

        message_type = payload.get("type")
        if message_type == DISPATCH_DUE_EVENTS:
            if set(payload) != {"version", "type"}:
                raise MalformedQueueMessage("The dispatcher tick has unexpected fields.")
            return cls.dispatch_tick()
        if message_type == DELIVER_SCHEDULED_EVENT:
            if set(payload) != {"version", "type", "event_id"}:
                raise MalformedQueueMessage("The delivery message has unexpected fields.")
            try:
                return cls.deliver_event(payload["event_id"])
            except ValueError:
                raise MalformedQueueMessage(
                    "The delivery message event identifier is invalid."
                ) from None
        raise MalformedQueueMessage("The queue message type is unsupported.")


@dataclass(frozen=True)
class ReceivedQueueMessage:
    body: str
    receipt_handle: str
    receive_count: int
    message_id: str | None = None


class DeliveryQueue(Protocol):
    def publish(self, envelope: QueueEnvelope) -> None: ...

    def receive(
        self,
        *,
        max_messages: int,
        wait_time_seconds: int,
        visibility_timeout: int,
    ) -> tuple[ReceivedQueueMessage, ...]: ...

    def delete(self, message: ReceivedQueueMessage) -> None: ...

    def change_visibility(
        self,
        message: ReceivedQueueMessage,
        *,
        visibility_timeout: int,
    ) -> None: ...
