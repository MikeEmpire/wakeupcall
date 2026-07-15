import pytest

from apps.delivery.queueing import MalformedQueueMessage, QueueEnvelope


def test_delivery_envelope_round_trip_contains_identifier_only():
    envelope = QueueEnvelope.deliver_event(42)

    body = envelope.to_json()

    assert body == '{"event_id":42,"type":"deliver_scheduled_event","version":1}'
    assert QueueEnvelope.from_json(body) == envelope


def test_dispatch_tick_round_trip_has_no_domain_state():
    envelope = QueueEnvelope.dispatch_tick()

    assert envelope.to_json() == '{"type":"dispatch_due_events","version":1}'
    assert QueueEnvelope.from_json(envelope.to_json()) == envelope


@pytest.mark.parametrize(
    "body",
    [
        "not-json",
        "[]",
        '{"type":"dispatch_due_events","version":2}',
        '{"extra":1,"type":"dispatch_due_events","version":1}',
        '{"event_id":true,"type":"deliver_scheduled_event","version":1}',
        '{"event_id":0,"type":"deliver_scheduled_event","version":1}',
        '{"type":"unknown","version":1}',
    ],
)
def test_rejects_malformed_or_unsupported_envelopes(body):
    with pytest.raises(MalformedQueueMessage):
        QueueEnvelope.from_json(body)


def test_rejects_invalid_event_identifier_before_serialization():
    with pytest.raises(ValueError):
        QueueEnvelope.deliver_event(-1)
