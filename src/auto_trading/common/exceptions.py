class AutoTradingError(Exception):
    """Base exception for the application."""


class FailSafeTriggered(AutoTradingError):
    """Raised when new orders must be blocked."""


class BrokerApiError(AutoTradingError):
    """Raised when broker API communication fails."""


class BrokerResponseError(AutoTradingError):
    """Raised when broker API returns an unexpected payload."""
