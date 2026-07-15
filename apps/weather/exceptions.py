class WeatherProviderError(RuntimeError):
    retryable = False


class WeatherConfigurationError(WeatherProviderError):
    pass


class WeatherLocationNotFound(WeatherProviderError):
    pass


class WeatherAuthenticationError(WeatherProviderError):
    pass


class WeatherRateLimited(WeatherProviderError):
    retryable = True


class WeatherProviderTimeout(WeatherProviderError):
    retryable = True


class WeatherProviderUnavailable(WeatherProviderError):
    retryable = True


class WeatherMalformedResponse(WeatherProviderError):
    retryable = True
