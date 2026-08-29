"""Tests for the Rain Bird IQ4 client."""

from pyrainbird_iq4.client import RainBirdIQ4Client


def test_default_base_url() -> None:
    """Default HTTPS port should not be included explicitly."""
    client = RainBirdIQ4Client("10.0.0.10")

    assert client.base_url == "https://10.0.0.10"


def test_nonstandard_base_url() -> None:
    """Nonstandard HTTPS ports should be included."""
    client = RainBirdIQ4Client("10.0.0.10", port=8443)

    assert client.base_url == "https://10.0.0.10:8443"
