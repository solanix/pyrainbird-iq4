"""Exceptions for pyrainbird-iq4."""

from __future__ import annotations

import re

_SENSITIVE_VALUE_RE = re.compile(
    r"(?i)(access_token|id_token|username|password|authorization|cookie)"
    r"\s*(?:=|:)\s*(?:bearer\s+)?[^&#\s,;]+"
)


def safe_error_detail(error: BaseException) -> str:
    """Return an exception detail with credential-like values redacted."""
    detail = str(error) or type(error).__name__
    return _SENSITIVE_VALUE_RE.sub(r"\1=<redacted>", detail)


class RainBirdIQ4Error(Exception):
    """Base exception for Rain Bird IQ4 errors."""


class RainBirdIQ4APIError(RainBirdIQ4Error):
    """Raised when an IQ4 API endpoint returns an error response."""

    def __init__(self, endpoint: str, status: int) -> None:
        self.endpoint = endpoint.split("?", 1)[0].split("#", 1)[0]
        self.status = status
        super().__init__(f"IQ4 API request failed: {self.endpoint} returned HTTP {status}")


class RainBirdIQ4ForbiddenError(RainBirdIQ4APIError):
    """Raised when an authenticated endpoint denies access."""

    def __init__(self, endpoint: str) -> None:
        super().__init__(endpoint, 403)


class RainBirdIQ4UnsupportedEndpointError(RainBirdIQ4APIError):
    """Raised when an IQ4 API endpoint is not available."""

    def __init__(self, endpoint: str, status: int = 404) -> None:
        super().__init__(endpoint, status)


class RainBirdIQ4ConnectionError(RainBirdIQ4Error):
    """Raised when the IQ4 controller cannot be reached."""


class RainBirdIQ4AuthenticationError(RainBirdIQ4Error):
    """Raised when authentication fails."""


class RainBirdIQ4CredentialsError(RainBirdIQ4AuthenticationError):
    """Raised when IQ4 rejects the supplied credentials."""


class RainBirdIQ4WAFChallengeError(RainBirdIQ4AuthenticationError):
    """Raised when AWS WAF blocks an IQ4 login attempt."""
