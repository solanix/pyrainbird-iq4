"""Tests for the read-only local IQ4 diagnostic."""

import errno
from dataclasses import dataclass
from typing import Any

import pytest
from pyrainbird.exceptions import (  # type: ignore[import-untyped]
    RainbirdAuthException,
    RainbirdConnectionError,
    RainbirdDeviceNackError,
)

from pyrainbird_iq4 import local_diagnostic


@dataclass
class _Mask:
    active_set: set[int]


class _Controller:
    def __init__(self, *, connection_failure: bool = False, queue_failure: bool = False) -> None:
        self.connection_failure = connection_failure
        self.queue_failure = queue_failure
        self.calls: list[str] = []

    async def get_current_irrigation(self) -> bool:
        self.calls.append("irrigation")
        if self.connection_failure:
            raise RainbirdConnectionError("secret host") from ConnectionResetError()
        return False

    async def get_zone_states(self) -> _Mask:
        self.calls.append("zones")
        return _Mask({2})

    async def get_available_stations(self) -> _Mask:
        self.calls.append("available")
        return _Mask({1, 2})

    async def get_rain_sensor_state(self) -> bool:
        self.calls.append("rain_sensor")
        return False

    async def get_rain_delay(self) -> int:
        self.calls.append("rain_delay")
        return 0

    async def _process_command(self, _render: Any, _command: str, page: int) -> dict[str, Any]:
        self.calls.append(f"queue_{page}")
        if page == 0 and self.queue_failure:
            raise RainbirdDeviceNackError("password=do-not-print")
        return {"program": {"program": 1, "zone": 2, "seconds": 30}}


async def test_authentication_failure_is_reported_without_secret(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A rejected handshake never claims authentication succeeded."""

    async def reject(_session: Any, _host: str, _password: str) -> Any:
        raise RainbirdAuthException("credential=246810")

    monkeypatch.setattr(local_diagnostic, "_connect", reject)

    assert await local_diagnostic._run("192.0.2.10", "246810") == 1
    output = capsys.readouterr().out
    assert "Local authentication: FAILED" in output
    assert "Local authentication: OK" not in output
    assert "246810" not in output


async def test_transport_failure_after_authentication_skips_later_reads(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A lost connection is distinguished from a successful handshake."""
    controller = _Controller(connection_failure=True)

    async def connect(_session: Any, _host: str, _password: str) -> Any:
        return controller, local_diagnostic.LocalTransport("https", 443)

    monkeypatch.setattr(local_diagnostic, "_connect", connect)

    assert await local_diagnostic._run("192.0.2.10", "secret") == 1
    output = capsys.readouterr().out
    assert "Local authentication: OK" in output
    assert "Local irrigation enabled flag: FAILED (connection reset)" in output
    assert "Local active terminals: SKIPPED (connection unusable)" in output
    assert controller.calls == ["irrigation"]
    assert "secret" not in output


async def test_optional_failure_continues_to_later_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unsupported queue page does not prevent the next page probe."""
    controller = _Controller(queue_failure=True)

    async def connect(_session: Any, _host: str, _password: str) -> Any:
        return controller, local_diagnostic.LocalTransport("http", 80)

    monkeypatch.setattr(local_diagnostic, "_connect", connect)

    assert await local_diagnostic._run("192.0.2.10", "secret") == 0
    output = capsys.readouterr().out
    assert "Local current queue page 0: FAILED (unsupported command)" in output
    assert "Local current queue page 1: {'program': 1, 'seconds': 30, 'zone': 2}" in output
    assert controller.calls[-2:] == ["queue_0", "queue_1"]
    assert "do-not-print" not in output


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        pytest.param(TimeoutError("token=secret"), "timeout", id="timeout"),
        pytest.param(
            ConnectionRefusedError(errno.ECONNREFUSED, "password=secret"),
            "connection refused",
            id="refused",
        ),
        pytest.param(
            UnicodeDecodeError("utf-8", b"secret", 0, 1, "bad"),
            "malformed response",
            id="decode",
        ),
    ],
)
def test_safe_failure_never_renders_exception_details(error: BaseException, expected: str) -> None:
    """Only fixed failure classifications are returned."""
    result = local_diagnostic._safe_failure(error)

    assert result == expected
    assert "secret" not in result
