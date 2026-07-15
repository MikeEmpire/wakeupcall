import logging

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from apps.delivery.queueing import (
    DeliveryQueue,
    QueueConfigurationError,
    QueueEnvelope,
    QueueUnavailable,
    ReceivedQueueMessage,
)

logger = logging.getLogger(__name__)


class SqsDeliveryQueue(DeliveryQueue):
    """SQS adapter that keeps AWS response objects inside the boundary."""

    def __init__(
        self,
        *,
        queue_url: str,
        region_name: str,
        connect_timeout: float = 2.0,
        read_timeout: float = 25.0,
        client=None,
    ):
        if not queue_url or not region_name:
            raise QueueConfigurationError("The SQS queue URL and AWS region are required.")
        if connect_timeout <= 0 or read_timeout <= 0:
            raise QueueConfigurationError("SQS timeouts must be positive.")

        self.queue_url = queue_url
        if client is None:
            client = boto3.client(
                "sqs",
                region_name=region_name,
                config=Config(
                    connect_timeout=connect_timeout,
                    read_timeout=read_timeout,
                    retries={"max_attempts": 2, "mode": "standard"},
                ),
            )
        self.client = client

    @classmethod
    def from_settings(cls):
        return cls(
            queue_url=settings.DELIVERY_QUEUE_URL,
            region_name=settings.AWS_REGION,
            connect_timeout=settings.DELIVERY_QUEUE_CONNECT_TIMEOUT,
            read_timeout=settings.DELIVERY_QUEUE_READ_TIMEOUT,
        )

    def publish(self, envelope: QueueEnvelope) -> None:
        try:
            self.client.send_message(
                QueueUrl=self.queue_url,
                MessageBody=envelope.to_json(),
            )
        except (BotoCoreError, ClientError):
            raise QueueUnavailable("The delivery queue could not accept a message.") from None
        logger.info(
            "Queue message published: type=%s event_id=%s",
            envelope.message_type,
            envelope.event_id,
        )

    def receive(
        self,
        *,
        max_messages: int,
        wait_time_seconds: int,
        visibility_timeout: int,
    ) -> tuple[ReceivedQueueMessage, ...]:
        if not 1 <= max_messages <= 10:
            raise ValueError("SQS receive batch size must be between 1 and 10.")
        if not 0 <= wait_time_seconds <= 20:
            raise ValueError("SQS wait time must be between 0 and 20 seconds.")
        if not 0 <= visibility_timeout <= 43200:
            raise ValueError("SQS visibility timeout must be between 0 and 43200 seconds.")
        try:
            response = self.client.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=max_messages,
                WaitTimeSeconds=wait_time_seconds,
                VisibilityTimeout=visibility_timeout,
                AttributeNames=["ApproximateReceiveCount"],
            )
        except (BotoCoreError, ClientError):
            raise QueueUnavailable("The delivery queue could not be read.") from None

        if not isinstance(response, dict):
            raise QueueUnavailable("The delivery queue returned an invalid response.")
        raw_messages = response.get("Messages", [])
        if not isinstance(raw_messages, list):
            raise QueueUnavailable("The delivery queue returned an invalid response.")

        messages = []
        for raw_message in raw_messages:
            if not isinstance(raw_message, dict):
                raise QueueUnavailable(
                    "The delivery queue returned malformed message metadata."
                )
            try:
                body = raw_message["Body"]
                receipt_handle = raw_message["ReceiptHandle"]
                receive_count = int(raw_message["Attributes"]["ApproximateReceiveCount"])
            except (KeyError, TypeError, ValueError):
                raise QueueUnavailable(
                    "The delivery queue returned malformed message metadata."
                ) from None
            if (
                not isinstance(body, str)
                or not isinstance(receipt_handle, str)
                or receive_count < 1
            ):
                raise QueueUnavailable(
                    "The delivery queue returned malformed message metadata."
                )
            messages.append(
                ReceivedQueueMessage(
                    body=body,
                    receipt_handle=receipt_handle,
                    receive_count=receive_count,
                    message_id=raw_message.get("MessageId"),
                )
            )
        return tuple(messages)

    def delete(self, message: ReceivedQueueMessage) -> None:
        try:
            self.client.delete_message(
                QueueUrl=self.queue_url,
                ReceiptHandle=message.receipt_handle,
            )
        except (BotoCoreError, ClientError):
            raise QueueUnavailable("The delivery queue message could not be deleted.") from None

    def change_visibility(
        self,
        message: ReceivedQueueMessage,
        *,
        visibility_timeout: int,
    ) -> None:
        if not 0 <= visibility_timeout <= 43200:
            raise ValueError("SQS visibility timeout must be between 0 and 43200 seconds.")
        try:
            self.client.change_message_visibility(
                QueueUrl=self.queue_url,
                ReceiptHandle=message.receipt_handle,
                VisibilityTimeout=visibility_timeout,
            )
        except (BotoCoreError, ClientError):
            raise QueueUnavailable(
                "The delivery queue message visibility could not be changed."
            ) from None
