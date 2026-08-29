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


class RainBirdIQ4ConnectionError(RainBirdIQ4Error):
    """Raised when the IQ4 controller cannot be reached."""


class RainBirdIQ4AuthenticationError(RainBirdIQ4Error):
    """Raised when authentication fails."""
