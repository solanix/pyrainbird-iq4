"""Read-only local live-state access for Rain Bird IQ4 controllers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter
from typing import Any, Protocol, TypeVar

import aiohttp
from aiohttp.client_exceptions import ClientConnectionError, ServerDisconnectedError
from pyrainbird.async_client import (  # type: ignore[import-untyped]
    AsyncRainbirdClient,
    AsyncRainbirdController,
)
from pyrainbird.exceptions import (  # type: ignore[import-untyped]
    RainbirdApiException,
    RainbirdConnectionError,
)

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


class _NoKeepAliveSession:
    """Apply connection-close semantics only to local controller requests."""

    def __init__(self, session: aiohttp.ClientSession, controller_id: int | None) -> None:
        self._session = session
        self._controller_id = controller_id

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Request a fresh connection and safely report response lifecycle data."""
        headers = dict(kwargs.pop("headers", {}))
        headers["Connection"] = "close"
        _LOGGER.debug(
            "IQ4 local transport controller=%s status=new_connection_requested keepalive=false",
            self._controller_id,
        )
        try:
            response = await self._session.request(method, url, headers=headers, **kwargs)
        except Exception as err:
            _LOGGER.debug(
                "IQ4 local transport controller=%s status=failed exception=%s peer_closed=%s",
                self._controller_id,
                type(err).__name__,
                isinstance(err, (ServerDisconnectedError, ClientConnectionError)),
            )
            raise
        connection_header = response.headers.get("Connection", "absent")
        _LOGGER.debug(
            "IQ4 local transport controller=%s status=response "
            "connection_header=%s keepalive=false",
            self._controller_id,
            connection_header,
        )
        return response


class _StateMask(Protocol):
    """A Rain Bird terminal-state bitmask."""

    @property
    def active_set(self) -> set[int]: ...


class _AvailableStations(Protocol):
    """Available station response."""

    @property
    def active_set(self) -> set[int]: ...


class LocalController(Protocol):
    """Read operations required from the local SIP controller."""

    async def get_current_irrigation(self) -> bool: ...

    async def get_zone_states(self) -> _StateMask: ...

    async def get_available_stations(self) -> _AvailableStations: ...

    async def get_rain_sensor_state(self) -> bool: ...

    async def get_rain_delay(self) -> int: ...

    async def _process_command(
        self,
        render: Any,
        command: str,
        page: int,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class LocalIrrigationState:
    """Current read-only state obtained directly from the controller."""

    irrigation_enabled: bool | None
    active_terminals: tuple[int, ...]
    available_terminals: tuple[int, ...] | None
    rain_sensor_active: bool | None
    rain_delay_days: int | None
    queue: LocalQueueState | None = None
    failed_commands: frozenset[str] = frozenset()
    transport_failure: bool = False

    @property
    def state(self) -> LocalRunState:
        """Derive a conservative run state from corroborating live fields."""
        return derive_run_state(self.active_terminals, self.queue)

    @property
    def remaining_runtime_seconds(self) -> int | None:
        """Return runtime for the queued record matching the active terminal."""
        if (
            self.state is not LocalRunState.RUNNING
            or self.active_terminal is None
            or self.queue is None
            or self.queue.zones is None
        ):
            return None
        zone = next(
            (zone for zone in self.queue.zones if zone.terminal == self.active_terminal),
            None,
        )
        return zone.remaining_runtime_seconds if zone is not None else None

    @property
    def active_program_number(self) -> int | None:
        """Return the authoritative page-one program number for the active zone."""
        if (
            self.state is not LocalRunState.RUNNING
            or self.active_terminal is None
            or self.queue is None
            or self.queue.zones is None
        ):
            return None
        zone = next(
            (zone for zone in self.queue.zones if zone.terminal == self.active_terminal),
            None,
        )
        return zone.program_number if zone is not None else None

    @property
    def active_terminal(self) -> int | None:
        """Return the running page-one zone, falling back to the active mask."""
        if self.state is not LocalRunState.RUNNING:
            return None
        if self.queue is not None and self.queue.zones:
            matching = next(
                (
                    zone.terminal
                    for zone in self.queue.zones
                    if zone.terminal in self.active_terminals
                ),
                None,
            )
            if matching is not None:
                return matching
            if (
                len(self.queue.zones) == 1
                and self.queue.current is not None
                and (self.queue.current.running is True or self.queue.current.active is True)
            ):
                return self.queue.zones[0].terminal
        return self.active_terminals[0] if len(self.active_terminals) == 1 else None


class LocalRunState(StrEnum):
    """Conservative high-level local irrigation state."""

    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class LocalCurrentQueue:
    """Decoded page-zero current program state."""

    program_code: int | None
    running: bool | None
    active: bool | None
    terminal: int | None
    seconds: int | None
    zones_remaining: int | None

    @classmethod
    def from_response(cls, response: dict[str, Any]) -> LocalCurrentQueue | None:
        """Parse a supported page-zero response without inventing missing fields."""
        program = response.get("program")
        if not isinstance(program, dict):
            return None
        return cls(
            program_code=_optional_int(program.get("program")),
            running=_optional_bool(program.get("running")),
            active=_optional_bool(program.get("active")),
            terminal=_optional_int(program.get("zone")),
            seconds=_optional_int(program.get("seconds")),
            zones_remaining=_optional_int(program.get("zonesRemaining")),
        )


@dataclass(frozen=True, slots=True)
class LocalQueuedZone:
    """One decoded page-one queued zone in controller order."""

    order: int
    terminal: int
    remaining_runtime_seconds: int
    program_number: int | None

    @property
    def program_code(self) -> int | None:
        """Return the program number under its former compatibility name."""
        return self.program_number


@dataclass(frozen=True, slots=True)
class LocalQueueState:
    """Typed current and pending irrigation queue data."""

    current: LocalCurrentQueue | None
    zones: tuple[LocalQueuedZone, ...] | None

    @classmethod
    def from_responses(
        cls,
        page_zero: dict[str, Any] | None,
        page_one: dict[str, Any] | None,
    ) -> LocalQueueState:
        """Parse supported decoded queue pages, retaining partial-data uncertainty."""
        current = LocalCurrentQueue.from_response(page_zero) if page_zero else None
        zones_value = page_one.get("zones") if page_one else None
        if not isinstance(zones_value, list):
            return cls(current=current, zones=None)
        zones: list[LocalQueuedZone] = []
        for order, item in enumerate(zones_value, start=1):
            if not isinstance(item, dict):
                continue
            terminal = _optional_int(item.get("zone"))
            seconds = _optional_int(item.get("seconds"))
            if terminal is None or seconds is None:
                continue
            zones.append(
                LocalQueuedZone(
                    order=order,
                    terminal=terminal,
                    remaining_runtime_seconds=seconds,
                    program_number=_optional_int(item.get("program")),
                )
            )
        return cls(current=current, zones=tuple(zones))


def derive_run_state(
    active_terminals: tuple[int, ...], queue: LocalQueueState | None
) -> LocalRunState:
    """Derive state without treating the controller-enabled flag as activity."""
    if active_terminals:
        return LocalRunState.RUNNING
    if queue is None or queue.current is None or queue.zones is None:
        return LocalRunState.UNKNOWN
    current = queue.current
    if current.running is True or current.active is True:
        return LocalRunState.RUNNING
    explicitly_stopped = current.running is False or current.active is False
    no_remaining = current.zones_remaining in (None, 0)
    if explicitly_stopped and no_remaining and not queue.zones:
        return LocalRunState.IDLE
    return LocalRunState.UNKNOWN


def _optional_int(value: object) -> int | None:
    """Return a protocol integer, excluding booleans and malformed values."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _optional_bool(value: object) -> bool | None:
    """Return a protocol boolean when explicitly present."""
    return value if isinstance(value, bool) else None


class RainBirdIQ4LocalClient:
    """Read-only facade over the established Rain Bird local SIP client."""

    def __init__(self, controller: LocalController, controller_id: int | None = None) -> None:
        """Initialize the local live-state client."""
        self._controller = controller
        self._controller_id = controller_id
        self._request_lock = asyncio.Lock()
        self._snapshot_count = 0

    @classmethod
    async def connect(
        cls,
        session: aiohttp.ClientSession,
        host: str,
        password: str,
        controller_id: int | None = None,
    ) -> RainBirdIQ4LocalClient:
        """Connect to an IQ4 controller over HTTPS without an HTTP fallback."""
        normalized_host = host.strip().rstrip("/")
        local_client = AsyncRainbirdClient(
            _NoKeepAliveSession(session, controller_id),
            f"https://{normalized_host}/stick",
            password,
            ssl_context=False,
        )
        controller = AsyncRainbirdController(local_client)
        await controller.get_model_and_version()
        _LOGGER.debug(
            "IQ4 local client controller=%s status=created transport_mode=connection_close",
            controller_id,
        )
        return cls(controller, controller_id)

    def _log_snapshot_client_reuse(self, kind: str) -> None:
        """Report client reuse separately from deliberately disabled TCP reuse."""
        self._snapshot_count += 1
        _LOGGER.debug(
            "IQ4 local client controller=%s status=reused snapshot=%s sequence=%s "
            "tcp_keepalive=false",
            self._controller_id,
            kind,
            self._snapshot_count,
        )

    async def get_realtime_state(self) -> LocalIrrigationState:
        """Fetch the minimal realtime snapshot, skipping queue page one while idle."""
        async with self._request_lock:
            self._log_snapshot_client_reuse("realtime")
            failures: set[str] = set()
            transport_failure = False
            active_ok, zone_states, transport = await self._run_command(
                "active_terminals", self._controller.get_zone_states
            )
            if not active_ok:
                failures.add("active_terminals")
            transport_failure |= transport
            active_terminals = tuple(sorted(zone_states.active_set)) if active_ok else ()
            page_zero_ok, page_zero, transport = await self._run_command(
                "current_queue_page_0", lambda: self._get_queue_page(0)
            )
            if not page_zero_ok:
                failures.add("current_queue_page_0")
            transport_failure |= transport
            current = LocalCurrentQueue.from_response(page_zero) if page_zero_ok else None
            running = bool(
                active_terminals or current and (current.running is True or current.active is True)
            )
            page_one: dict[str, Any] | None = {"zones": []} if not running else None
            if running:
                page_one_ok, page_one_value, transport = await self._run_command(
                    "current_queue_page_1", lambda: self._get_queue_page(1)
                )
                if page_one_ok:
                    page_one = page_one_value
                else:
                    failures.add("current_queue_page_1")
                transport_failure |= transport
            return LocalIrrigationState(
                irrigation_enabled=None,
                active_terminals=active_terminals,
                available_terminals=None,
                rain_sensor_active=None,
                rain_delay_days=None,
                queue=LocalQueueState.from_responses(page_zero if page_zero_ok else None, page_one),
                failed_commands=frozenset(failures),
                transport_failure=transport_failure,
            )

    async def get_slow_state(self) -> LocalIrrigationState:
        """Fetch independently optional local configuration and sensor values."""
        async with self._request_lock:
            self._log_snapshot_client_reuse("slow")
            failures: set[str] = set()
            transport_failure = False

            async def read(name: str, call: Callable[[], Awaitable[_T]]) -> _T | None:
                nonlocal transport_failure
                ok, value, transport = await self._run_command(name, call)
                transport_failure |= transport
                if not ok:
                    failures.add(name)
                    return None
                return value

            enabled = await read("irrigation_enabled", self._controller.get_current_irrigation)
            available = await read("available_terminals", self._controller.get_available_stations)
            rain_sensor = await read("rain_sensor", self._controller.get_rain_sensor_state)
            rain_delay = await read("rain_delay", self._controller.get_rain_delay)
            return LocalIrrigationState(
                irrigation_enabled=enabled,
                active_terminals=(),
                available_terminals=(
                    tuple(sorted(available.active_set)) if available is not None else None
                ),
                rain_sensor_active=rain_sensor,
                rain_delay_days=rain_delay,
                failed_commands=frozenset(failures),
                transport_failure=transport_failure,
            )

    async def _run_command(
        self, name: str, call: Callable[[], Awaitable[_T]]
    ) -> tuple[bool, _T, bool]:
        """Run and safely log one serialized local RPC command."""
        started = perf_counter()
        try:
            value = await call()
        except Exception as err:
            elapsed_ms = round((perf_counter() - started) * 1000)
            _LOGGER.debug(
                "IQ4 local command controller=%s command=%s status=failed "
                "exception=%s elapsed_ms=%s",
                self._controller_id,
                name,
                type(err).__name__,
                elapsed_ms,
            )
            return (
                False,
                None,  # type: ignore[return-value]
                isinstance(err, (RainbirdApiException, RainbirdConnectionError)),
            )
        elapsed_ms = round((perf_counter() - started) * 1000)
        _LOGGER.debug(
            "IQ4 local command controller=%s command=%s status=ok elapsed_ms=%s",
            self._controller_id,
            name,
            elapsed_ms,
        )
        return True, value, False

    async def get_live_state(self) -> LocalIrrigationState:
        """Fetch supported local state serially to avoid controller overload."""
        async with self._request_lock:
            return await self._get_live_state()

    async def _get_live_state(self) -> LocalIrrigationState:
        """Fetch one complete snapshot while holding the client request lock."""
        irrigation_enabled = await self._controller.get_current_irrigation()
        zone_states = await self._controller.get_zone_states()
        available = await self._controller.get_available_stations()
        rain_sensor_active = await self._controller.get_rain_sensor_state()
        rain_delay_days = await self._controller.get_rain_delay()
        page_zero = await self._get_queue_page(0)
        page_one = await self._get_queue_page(1)
        return LocalIrrigationState(
            irrigation_enabled=irrigation_enabled,
            active_terminals=tuple(sorted(zone_states.active_set)),
            available_terminals=tuple(sorted(available.active_set)),
            rain_sensor_active=rain_sensor_active,
            rain_delay_days=rain_delay_days,
            queue=LocalQueueState.from_responses(page_zero, page_one),
        )

    async def _get_queue_page(self, page: int) -> dict[str, Any]:
        """Read one already-decoded queue page through the upstream SIP client."""
        return await self._controller._process_command(
            _queue_response,
            "CurrentQueueRequest",
            page,
        )


def _queue_response(response: dict[str, Any]) -> dict[str, Any]:
    """Return an already-decoded queue response."""
    return response
