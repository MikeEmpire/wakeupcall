from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class VerificationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    provider_sid: str | None = None


class PhoneVerificationGateway(Protocol):
    def start_verification(self, phone_number: str) -> VerificationResult: ...

    def check_verification(
        self,
        phone_number: str,
        code: str,
    ) -> VerificationResult: ...
