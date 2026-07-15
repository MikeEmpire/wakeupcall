from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError, ConnectTimeoutError
from django.test import override_settings

from apps.delivery.queueing import (
    QueueConfigurationError,
    QueueEnvelope,
    QueueUnavailable,
    ReceivedQueueMessage,
)
from apps.delivery.sqs import SqsDeliveryQueue

QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/delivery"


@pytest.fixture
def client():
    return Mock()


@pytest.fixture
def queue(client):
    return SqsDeliveryQueue(
        queue_url=QUEUE_URL,
        region_name="us-east-1",
        client=client,
    )


def client_error():
    return ClientError(
        {"Error": {"Code": "AccessDenied", "Message": "secret provider detail"}},
        "SendMessage",
    )


def test_publish_serializes_project_envelope(queue, client):
    queue.publish(QueueEnvelope.deliver_event(7))

    client.send_message.assert_called_once_with(
        QueueUrl=QUEUE_URL,
        MessageBody='{\"event_id\":7,\"type\":\"deliver_scheduled_event\",\"version\":1}',
    )


def test_receive_maps_only_safe_message_fields(queue, client):
    client.receive_message.return_value = {
        "Messages": [
            {
                "Body": '{"type":"dispatch_due_events","version":1}',
                "ReceiptHandle": "receipt-secret",
                "MessageId": "message-1",
                "Attributes": {"ApproximateReceiveCount": "2"},
                "ProviderSpecificField": "must not leak",
            }
        ]
    }

    messages = queue.receive(
        max_messages=10,
        wait_time_seconds=20,
        visibility_timeout=120,
    )

    assert messages == (
        ReceivedQueueMessage(
            body='{"type":"dispatch_due_events","version":1}',
            receipt_handle="receipt-secret",
            receive_count=2,
            message_id="message-1",
        ),
    )
    client.receive_message.assert_called_once_with(
        QueueUrl=QUEUE_URL,
        MaxNumberOfMessages=10,
        WaitTimeSeconds=20,
        VisibilityTimeout=120,
        AttributeNames=["ApproximateReceiveCount"],
    )


@pytest.mark.parametrize("response", [None, {"Messages": "invalid"}])
def test_receive_rejects_malformed_aws_response(queue, client, response):
    client.receive_message.return_value = response

    with pytest.raises(QueueUnavailable):
        queue.receive(
            max_messages=1,
            wait_time_seconds=0,
            visibility_timeout=120,
        )


def test_delete_and_visibility_use_receipt_handle(queue, client):
    message = ReceivedQueueMessage("body", "receipt-secret", 1)

    queue.change_visibility(message, visibility_timeout=30)
    queue.delete(message)

    client.change_message_visibility.assert_called_once_with(
        QueueUrl=QUEUE_URL,
        ReceiptHandle="receipt-secret",
        VisibilityTimeout=30,
    )
    client.delete_message.assert_called_once_with(
        QueueUrl=QUEUE_URL,
        ReceiptHandle="receipt-secret",
    )


@pytest.mark.parametrize(
    "provider_error",
    [client_error(), ConnectTimeoutError(endpoint_url="https://sqs.invalid")],
)
def test_maps_aws_failures_without_provider_details(queue, client, provider_error):
    client.send_message.side_effect = provider_error

    with pytest.raises(QueueUnavailable) as error:
        queue.publish(QueueEnvelope.dispatch_tick())

    assert "secret provider detail" not in str(error.value)


@pytest.mark.parametrize(
    ("max_messages", "wait", "visibility"),
    [(0, 20, 120), (11, 20, 120), (1, 21, 120), (1, 20, 43201)],
)
def test_receive_rejects_unbounded_parameters(
    queue, max_messages, wait, visibility
):
    with pytest.raises(ValueError):
        queue.receive(
            max_messages=max_messages,
            wait_time_seconds=wait,
            visibility_timeout=visibility,
        )


def test_rejects_missing_or_unbounded_configuration():
    with pytest.raises(QueueConfigurationError):
        SqsDeliveryQueue(queue_url="", region_name="")
    with pytest.raises(QueueConfigurationError):
        SqsDeliveryQueue(
            queue_url=QUEUE_URL,
            region_name="us-east-1",
            connect_timeout=0,
        )


@override_settings(
    DELIVERY_QUEUE_URL=QUEUE_URL,
    AWS_REGION="us-west-2",
    DELIVERY_QUEUE_CONNECT_TIMEOUT=3.0,
    DELIVERY_QUEUE_READ_TIMEOUT=26.0,
)
def test_builds_boto_client_from_settings(monkeypatch):
    boto_client = Mock()
    monkeypatch.setattr("apps.delivery.sqs.boto3.client", boto_client)

    queue = SqsDeliveryQueue.from_settings()

    assert queue.queue_url == QUEUE_URL
    _, kwargs = boto_client.call_args
    assert boto_client.call_args.args == ("sqs",)
    assert kwargs["region_name"] == "us-west-2"
    assert kwargs["config"].connect_timeout == 3.0
    assert kwargs["config"].read_timeout == 26.0


def test_publish_log_omits_body_and_receipt_data(queue, caplog):
    caplog.set_level("INFO", logger="apps.delivery.sqs")

    queue.publish(QueueEnvelope.deliver_event(9))

    assert "event_id=9" in caplog.text
    assert "deliver_scheduled_event" in caplog.text
    assert "version" not in caplog.text
