class DeliveryError(RuntimeError):
    retryable = False


class DeliveryConfigurationError(DeliveryError):
    pass


class DeliveryInputInvalid(DeliveryError):
    pass


class DeliveryAuthenticationError(DeliveryError):
    pass


class DeliveryRateLimited(DeliveryError):
    retryable = True


class DeliveryProviderRejected(DeliveryError):
    pass


class DeliveryProviderTimeout(DeliveryError):
    retryable = True


class DeliveryProviderUnavailable(DeliveryError):
    retryable = True


class DeliveryMalformedResponse(DeliveryError):
    retryable = True


class MissedDeliveryWindow(DeliveryError):
    pass
