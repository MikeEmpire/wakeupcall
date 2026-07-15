class VerificationError(RuntimeError):
    retryable = False


class VerificationConfigurationError(VerificationError):
    pass


class PhoneAlreadyVerified(VerificationError):
    pass


class PhoneNumberChanged(VerificationError):
    pass


class VerificationInputInvalid(VerificationError):
    pass


class VerificationExpired(VerificationError):
    pass


class VerificationBlocked(VerificationError):
    pass


class VerificationAuthenticationError(VerificationError):
    pass


class VerificationRateLimited(VerificationError):
    retryable = True


class VerificationProviderTimeout(VerificationError):
    retryable = True


class VerificationProviderUnavailable(VerificationError):
    retryable = True


class VerificationMalformedResponse(VerificationError):
    retryable = True
