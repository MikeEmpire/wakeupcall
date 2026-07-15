import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeliveryResult:
    provider_sid: str | None = None


class MessageSender(Protocol):
    def send(self, *, channel: str, to: str, message: str) -> DeliveryResult: ...


def mask_phone_number(number: str) -> str:
    if len(number) <= 4:
        return "*" * len(number)
    return f"{'*' * (len(number) - 4)}{number[-4:]}"


class DemoMessageSender:
    """Records delivery intent without contacting an external provider."""

    def send(self, *, channel: str, to: str, message: str) -> DeliveryResult:
        logger.info(
            "Demo delivery suppressed: channel=%s to=%s message=%s",
            channel,
            mask_phone_number(to),
            message,
        )
        return DeliveryResult()
