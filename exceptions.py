from src.models import ExportResult

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

class RetryableExportError(Exception):
    """Raised when export returns a retryable result, so tenacity can retry."""

    def __init__(self, result: ExportResult):
        self.result = result
        super().__init__(result.error.message if result.error else "Retryable error")
