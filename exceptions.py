class ConnectorError(Exception):
    """Base exception for connector errors."""

    pass


class AuthenticationError(ConnectorError):
    """Invalid or expired credentials."""

    pass


class RateLimitError(ConnectorError):
    """Rate limited by source/target."""

    def __init__(self, message: str, retry_after_seconds: int | None = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class SourceUnavailableError(ConnectorError):
    """Source API temporarily unavailable."""

    pass


class NormalizationError(ConnectorError):
    """Cannot normalize source data to unified model."""

    pass


class NotSupportedError(ConnectorError):
    """Operation not supported by this connector."""

    pass
