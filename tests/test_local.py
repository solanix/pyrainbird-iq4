"""Tests for read-only local IQ4 live state."""

import asyncio
import logging
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pyrainbird.exceptions import RainbirdApiException  # type: ignore[import-untyped]

from pyrainbird_iq4.local import (
    LocalIrrigationState,
    LocalQueueState,
    LocalRunState,
    RainBirdIQ4LocalClient,
    _NoKeepAliveSession,
    derive_run_state,
)


@dataclass
class _Mask:
    active_set: set[int]


class _Controller:
    def __init__(
        self,
        *,
        active: set[int] | None = None,
        running: bool = False,
        fail: str | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.active = {7, 2} if active is None else active
        self.running = running
        self.fail = fail

    async def get_current_irrigation(self) -> bool:
        self.calls.append("get_current_irrigation")
        if self.fail == "irrigation_enabled":
            raise RainbirdApiException("password=local-secret")
        return True

    async def get_zone_states(self) -> _Mask:
        self.calls.append("get_zone_states")
        if self.fail == "active_terminals":
            raise RainbirdApiException("password=local-secret")
        return _Mask(self.active)

    async def get_available_stations(self) -> _Mask:
        self.calls.append("get_available_stations")
        if self.fail == "available_terminals":
            raise RainbirdApiException("password=local-secret")
        return _Mask({3, 2, 1})

    async def get_rain_sensor_state(self) -> bool:
        self.calls.append("get_rain_sensor_state")
        if self.fail == "rain_sensor":
            raise RainbirdApiException("password=local-secret")
        return False

    async def get_rain_delay(self) -> int:
        self.calls.append("get_rain_delay")
        if self.fail == "rain_delay":
            raise RainbirdApiException("password=local-secret")
        return 0

    async def _process_command(
        self, _render: object, _command: str, page: int
    ) -> dict[str, object]:
        self.calls.append(f"get_queue_{page}")
        if self.fail == f"current_queue_page_{page}":
            raise RainbirdApiException("password=local-secret")
        if page == 0:
            return {
                "program": {
                    "program": 0,
                    "running": self.running,
                    "zonesRemaining": 1 if self.running else 0,
                }
            }
        return {
            "type": "CurrentQueueResponse",
            "zones": ([{"program": 1, "zone": 2, "seconds": 90}] if self.running else []),
        }


async def test_get_live_state_uses_only_read_operations_serially() -> None:
    """Local state combines established read-only SIP operations."""
    controller = _Controller()

    state = await RainBirdIQ4LocalClient(controller).get_live_state()

    assert state.irrigation_enabled is True
    assert state.active_terminals == (2, 7)
    assert state.available_terminals == (1, 2, 3)
    assert state.rain_sensor_active is False
    assert state.rain_delay_days == 0
    assert state.state is LocalRunState.RUNNING
    assert state.queue is not None
    assert state.queue.zones == ()
    assert controller.calls == [
        "get_current_irrigation",
        "get_zone_states",
        "get_available_stations",
        "get_rain_sensor_state",
        "get_rain_delay",
        "get_queue_0",
        "get_queue_1",
    ]


async def test_connect_uses_https_only_without_port_80_fallback() -> None:
    """Normal IQ4 connection builds only the known HTTPS transport."""
    session = MagicMock()
    protocol_client = MagicMock()
    controller = MagicMock()
    controller.get_model_and_version = AsyncMock()

    with (
        patch(
            "pyrainbird_iq4.local.AsyncRainbirdClient",
            return_value=protocol_client,
        ) as client_class,
        patch(
            "pyrainbird_iq4.local.AsyncRainbirdController",
            return_value=controller,
        ),
    ):
        client = await RainBirdIQ4LocalClient.connect(session, " controller.local ", "local-secret")

    assert isinstance(client, RainBirdIQ4LocalClient)
    transport_session = client_class.call_args.args[0]
    assert isinstance(transport_session, _NoKeepAliveSession)
    assert client_class.call_args.args[1:] == (
        "https://controller.local/stick",
        "local-secret",
    )
    assert client_class.call_args.kwargs == {"ssl_context": False}
    controller.get_model_and_version.assert_awaited_once_with()
    assert "http://" not in str(client_class.call_args)


class _ClosedKeepAliveServer:
    """Model a server that silently invalidates persistent connections."""

    def __init__(self) -> None:
        self.requests = 0
        self.headers: list[dict[str, str]] = []

    async def request(self, _method: str, _url: str, **kwargs: object) -> object:
        self.requests += 1
        headers = dict(kwargs.get("headers", {}))  # type: ignore[arg-type]
        self.headers.append(headers)
        if self.requests > 1 and headers.get("Connection") != "close":
            raise ConnectionResetError("stale pooled socket")
        return type("Response", (), {"headers": {"Connection": "close"}})()


async def test_connection_close_avoids_stale_keepalive_reuse(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Fresh-request transport succeeds where persistent reuse alternates."""
    caplog.set_level(logging.DEBUG, logger="pyrainbird_iq4.local")
    persistent_server = _ClosedKeepAliveServer()
    await persistent_server.request("post", "https://controller/stick", headers={})
    with pytest.raises(ConnectionResetError):
        await persistent_server.request("post", "https://controller/stick", headers={})

    force_close_server = _ClosedKeepAliveServer()
    transport = _NoKeepAliveSession(force_close_server, 200001)  # type: ignore[arg-type]
    await transport.request("post", "https://controller/stick", headers={})
    await transport.request("post", "https://controller/stick", headers={})

    assert force_close_server.requests == 2
    assert force_close_server.headers == [
        {"Connection": "close"},
        {"Connection": "close"},
    ]
    assert "status=new_connection_requested keepalive=false" in caplog.text
    assert "status=response connection_header=close keepalive=false" in caplog.text


async def test_idle_realtime_snapshot_skips_queue_page_one() -> None:
    """Idle realtime polling uses only active terminals and queue page zero."""
    controller = _Controller(active=set(), running=False)

    state = await RainBirdIQ4LocalClient(controller, 200001).get_realtime_state()

    assert state.state is LocalRunState.IDLE
    assert controller.calls == ["get_zone_states", "get_queue_0"]


async def test_running_realtime_snapshot_reads_queue_page_one() -> None:
    """Running realtime polling adds page one for zone, program, and runtime."""
    controller = _Controller(active={2}, running=True)

    state = await RainBirdIQ4LocalClient(controller, 200001).get_realtime_state()

    assert state.state is LocalRunState.RUNNING
    assert state.active_terminal == 2
    assert state.remaining_runtime_seconds == 90
    assert controller.calls == ["get_zone_states", "get_queue_0", "get_queue_1"]


async def test_queue_page_zero_failure_returns_partial_snapshot() -> None:
    """A page-zero failure is retained as field-level uncertainty without retry."""
    controller = _Controller(active=set(), running=False, fail="current_queue_page_0")

    state = await RainBirdIQ4LocalClient(controller, 200001).get_realtime_state()

    assert state.state is LocalRunState.UNKNOWN
    assert state.failed_commands == frozenset({"current_queue_page_0"})
    assert controller.calls == ["get_zone_states", "get_queue_0"]


async def test_slow_command_failure_is_partial_and_safely_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One slow RPC failure preserves unrelated values and logs no details."""
    controller = _Controller(active=set(), fail="rain_delay")
    caplog.set_level(logging.DEBUG, logger="pyrainbird_iq4.local")

    state = await RainBirdIQ4LocalClient(controller, 200001).get_slow_state()

    assert state.irrigation_enabled is True
    assert state.available_terminals == (1, 2, 3)
    assert state.rain_sensor_active is False
    assert state.rain_delay_days is None
    assert state.failed_commands == frozenset({"rain_delay"})
    assert controller.calls == [
        "get_current_irrigation",
        "get_available_stations",
        "get_rain_sensor_state",
        "get_rain_delay",
    ]
    assert "controller=200001 command=rain_delay status=failed" in caplog.text
    assert "exception=RainbirdApiException" in caplog.text
    assert "elapsed_ms=" in caplog.text
    assert "local-secret" not in caplog.text


async def test_overlapping_live_state_reads_are_serialized() -> None:
    """A second snapshot cannot issue commands during the first snapshot."""
    controller = _Controller()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    original = controller.get_current_irrigation
    call_count = 0

    async def blocking_irrigation() -> bool:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_started.set()
            await release_first.wait()
        return await original()

    controller.get_current_irrigation = blocking_irrigation  # type: ignore[method-assign]
    client = RainBirdIQ4LocalClient(controller)
    first = asyncio.create_task(client.get_live_state())
    await first_started.wait()
    second = asyncio.create_task(client.get_live_state())
    await asyncio.sleep(0)

    assert call_count == 1
    release_first.set()
    await asyncio.gather(first, second)
    assert call_count == 2


@pytest.mark.parametrize(
    ("active", "page_zero", "page_one", "expected"),
    [
        pytest.param(
            (),
            {"program": {"program": 0, "running": False, "zonesRemaining": 0}},
            {"type": "CurrentQueueResponse", "zones": []},
            LocalRunState.IDLE,
            id="idle-ambiguous-enabled-flag",
        ),
        pytest.param(
            (),
            {"program": {"program": 1, "running": True, "zonesRemaining": 2}},
            {"zones": []},
            LocalRunState.RUNNING,
            id="running-queue-without-active-terminal",
        ),
        pytest.param(
            (3,),
            {"program": {"program": 0, "running": False, "zonesRemaining": 0}},
            {"zones": []},
            LocalRunState.RUNNING,
            id="active-terminal",
        ),
        pytest.param(
            (),
            {"program": {"program": 1, "running": False, "zonesRemaining": 1}},
            {"zones": [{"program": 1, "zone": 4, "seconds": 120}]},
            LocalRunState.UNKNOWN,
            id="queued-not-proven-paused",
        ),
        pytest.param(
            (),
            {"program": {"program": "bad", "running": "bad"}},
            {"zones": [{"zone": 4}]},
            LocalRunState.UNKNOWN,
            id="malformed-partial",
        ),
    ],
)
def test_conservative_run_state(
    active: tuple[int, ...],
    page_zero: dict[str, object],
    page_one: dict[str, object],
    expected: LocalRunState,
) -> None:
    """Run state requires active terminals or explicit queue evidence."""
    queue = LocalQueueState.from_responses(page_zero, page_one)

    assert derive_run_state(active, queue) is expected


def test_queue_zone_fields_and_order_are_typed() -> None:
    """Page-one zones retain program, terminal, runtime, and queue order."""
    queue = LocalQueueState.from_responses(
        {"program": {"program": 2, "running": False, "zonesRemaining": 2}},
        {
            "type": "CurrentQueueResponse",
            "zones": [
                {"program": 2, "zone": 7, "seconds": 90},
                {"program": 2, "zone": 8, "seconds": 120},
            ],
        },
    )

    assert queue.current is not None
    assert queue.current.program_code == 2
    assert queue.current.zones_remaining == 2
    assert queue.zones is not None
    decoded_zones = [
        (
            zone.order,
            zone.program_number,
            zone.terminal,
            zone.remaining_runtime_seconds,
        )
        for zone in queue.zones
    ]
    assert decoded_zones == [
        (1, 2, 7, 90),
        (2, 2, 8, 120),
    ]


def test_remaining_runtime_matches_only_active_terminal() -> None:
    """Page-one seconds are exposed only for the active running terminal."""
    queue = LocalQueueState.from_responses(
        {"program": {"program": 4, "running": True, "zonesRemaining": 0}},
        {"zones": [{"program": 0, "zone": 3, "seconds": 42}]},
    )
    running = LocalIrrigationState(True, (3,), (1, 2, 3), False, 0, queue)
    idle_queue = LocalQueueState.from_responses(
        {"program": {"program": 0, "running": False, "zonesRemaining": 0}},
        {"zones": []},
    )
    idle = LocalIrrigationState(True, (), (1, 2, 3), False, 0, idle_queue)

    assert running.remaining_runtime_seconds == 42
    assert running.active_program_number == 0
    assert idle.remaining_runtime_seconds is None
    assert idle.active_program_number is None
    assert queue.current is not None
    assert queue.current.program_code == 4


@pytest.mark.parametrize(
    ("active", "page_zero", "page_one", "expected_state", "terminal", "remaining"),
    [
        pytest.param(
            (),
            {"program": {"program": 0, "running": False, "zonesRemaining": 0}},
            {"zones": []},
            LocalRunState.IDLE,
            None,
            None,
            id="observed-idle",
        ),
        pytest.param(
            (3,),
            {"program": {"program": 4, "running": True, "zonesRemaining": 0}},
            {"zones": [{"program": 0, "seconds": 42, "zone": 3}]},
            LocalRunState.RUNNING,
            3,
            42,
            id="observed-manual-zone-3",
        ),
        pytest.param(
            (2,),
            {"program": {"program": 4, "running": True, "zonesRemaining": 0}},
            {"zones": [{"program": 0, "seconds": 39, "zone": 2}]},
            LocalRunState.RUNNING,
            2,
            39,
            id="observed-manual-zone-2",
        ),
    ],
)
def test_observed_manual_run_samples(
    active: tuple[int, ...],
    page_zero: dict[str, object],
    page_one: dict[str, object],
    expected_state: LocalRunState,
    terminal: int | None,
    remaining: int | None,
) -> None:
    """Live manual runs retain raw codes without creating configured programs."""
    state = LocalIrrigationState(
        True,
        active,
        (1, 2, 3),
        False,
        0,
        LocalQueueState.from_responses(page_zero, page_one),
    )

    assert state.state is expected_state
    assert state.active_terminal == terminal
    assert state.remaining_runtime_seconds == remaining
    assert state.queue is not None
    if state.queue.current is not None:
        expected_code = 4 if expected_state is LocalRunState.RUNNING else 0
        assert state.queue.current.program_code == expected_code


@pytest.mark.parametrize(
    ("active", "page_zero", "page_one", "program_number", "remaining", "zones_remaining"),
    [
        pytest.param(
            (),
            {"program": {"program": 0, "running": False, "zonesRemaining": 0}},
            {"zones": []},
            None,
            None,
            0,
            id="idle",
        ),
        pytest.param(
            (2,),
            {"program": {"program": 4, "running": True, "zonesRemaining": 0}},
            {"zones": [{"program": 0, "seconds": 39, "zone": 2}]},
            0,
            39,
            0,
            id="manual-zone-2",
        ),
        pytest.param(
            (2,),
            {"program": {"program": 0, "running": True, "zonesRemaining": 7}},
            {"zones": [{"program": 1, "seconds": 1836, "zone": 2}]},
            1,
            1836,
            7,
            id="program-a",
        ),
        pytest.param(
            (5,),
            {"program": {"program": 0, "running": True, "zonesRemaining": 1}},
            {"zones": [{"program": 2, "seconds": 950, "zone": 5}]},
            2,
            950,
            1,
            id="program-b",
        ),
    ],
)
def test_observed_idle_manual_and_scheduled_samples(
    active: tuple[int, ...],
    page_zero: dict[str, object],
    page_one: dict[str, object],
    program_number: int | None,
    remaining: int | None,
    zones_remaining: int,
) -> None:
    """Observed samples establish page-one program and runtime semantics."""
    state = LocalIrrigationState(
        True,
        active,
        tuple(range(1, 11)),
        False,
        0,
        LocalQueueState.from_responses(page_zero, page_one),
    )

    assert state.active_program_number == program_number
    assert state.remaining_runtime_seconds == remaining
    assert state.queue is not None
    assert state.queue.current is not None
    assert state.queue.current.zones_remaining == zones_remaining
