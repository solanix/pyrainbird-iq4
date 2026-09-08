"""Safe read-only diagnostic for an IQ4 controller's local SIP protocol."""

from __future__ import annotations

import argparse
import asyncio
import errno
import getpass
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

import aiohttp
from pyrainbird.async_client import (  # type: ignore[import-untyped]
    AsyncRainbirdClient,
    AsyncRainbirdController,
)
from pyrainbird.exceptions import (  # type: ignore[import-untyped]
    RainbirdApiException,
    RainbirdAuthException,
    RainbirdCodingException,
    RainbirdConnectionError,
    RainbirdDeviceBusyException,
    RainbirdDeviceNackError,
)

from .local import LocalQueueState, derive_run_state

AUTH_METHOD = (
    "controller-local password/PIN; used as the AES key for encrypted "
    "JSON-RPC requests to the local /stick endpoint"
)


@dataclass(frozen=True, slots=True)
class LocalTransport:
    """Selected local controller transport."""

    scheme: str
    port: int


def _exception_chain(error: BaseException) -> list[BaseException]:
    """Return an exception chain without rendering possibly sensitive messages."""
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _safe_failure(error: BaseException) -> str:
    """Classify a failure without including exception text or request data."""
    chain = _exception_chain(error)
    if any(isinstance(item, RainbirdAuthException) for item in chain):
        return "authentication failure"
    if any(isinstance(item, (TimeoutError, asyncio.TimeoutError)) for item in chain):
        return "timeout"
    if any(
        isinstance(item, (ConnectionResetError, BrokenPipeError))
        or isinstance(item, OSError)
        and item.errno in (errno.ECONNRESET, errno.EPIPE)
        for item in chain
    ):
        return "connection reset"
    if any(
        isinstance(item, ConnectionRefusedError)
        or isinstance(item, OSError)
        and item.errno == errno.ECONNREFUSED
        for item in chain
    ):
        return "connection refused"
    if any(isinstance(item, RainbirdDeviceNackError) for item in chain):
        return "unsupported command"
    if any(isinstance(item, RainbirdDeviceBusyException) for item in chain):
        return "controller busy"
    if any(isinstance(item, (UnicodeError, json.JSONDecodeError)) for item in chain):
        return "malformed response"
    if any(isinstance(item, RainbirdCodingException) for item in chain):
        return "protocol decode error"
    if any(isinstance(item, RainbirdConnectionError) for item in chain):
        return "connection failure"
    if any(isinstance(item, aiohttp.ClientError) for item in chain):
        return "transport error"
    if any(isinstance(item, (ValueError, TypeError, KeyError)) for item in chain):
        return "protocol decode error"
    if any(isinstance(item, RainbirdApiException) for item in chain):
        return "protocol error"
    return "unexpected error"


def _transport_line(host: str, transport: LocalTransport, status: str) -> None:
    """Print non-secret transport metadata."""
    print(
        f"Local transport: host={host} network=TCP port={transport.port} "
        f"protocol={transport.scheme.upper()} encrypted-JSON-RPC/SIP "
        f"client=AsyncRainbirdController status={status}"
    )


async def _probe_transport(
    session: aiohttp.ClientSession,
    host: str,
    password: str,
    transport: LocalTransport,
) -> AsyncRainbirdController:
    """Prove a transport and credential with one read-only model request."""
    url = f"{transport.scheme}://{host}/stick"
    local_client = AsyncRainbirdClient(
        session,
        url,
        password,
        ssl_context=False if transport.scheme == "https" else None,
    )
    controller = AsyncRainbirdController(local_client)
    await controller.get_model_and_version()
    return controller


async def _connect(
    session: aiohttp.ClientSession, host: str, password: str
) -> tuple[AsyncRainbirdController, LocalTransport]:
    """Discover HTTPS or HTTP while retaining safe failure context."""
    https = LocalTransport("https", 443)
    try:
        controller = await _probe_transport(session, host, password, https)
    except RainbirdConnectionError as https_error:
        _transport_line(host, https, f"FAILED ({_safe_failure(https_error)})")
    else:
        return controller, https

    http = LocalTransport("http", 80)
    try:
        controller = await _probe_transport(session, host, password, http)
    except BaseException as http_error:
        _transport_line(host, http, f"FAILED ({_safe_failure(http_error)})")
        raise
    return controller, http


def _is_unusable_connection(error: BaseException) -> bool:
    """Return whether later requests cannot safely use the same connection."""
    return _safe_failure(error) in {
        "timeout",
        "connection reset",
        "connection refused",
        "connection failure",
        "transport error",
    }


async def _read[T](
    label: str,
    request: Callable[[], Awaitable[T]],
    render: Callable[[T], object] = lambda value: value,
) -> tuple[bool, T | None]:
    """Run and report one isolated read operation."""
    try:
        value = await request()
    except BaseException as error:
        print(f"{label}: FAILED ({_safe_failure(error)})")
        return not _is_unusable_connection(error), None
    print(f"{label}: {render(value)}")
    return True, value


def _safe_queue(value: dict[str, Any] | None) -> object:
    """Render only decoded queue fields, never an undecoded raw payload."""
    if value is None:
        return "no decoded response"
    allowed = {"active", "program", "running", "seconds", "zone", "zonesRemaining"}
    if (program := value.get("program")) and isinstance(program, dict):
        return {key: program[key] for key in sorted(program.keys() & allowed)}
    if isinstance(zones := value.get("zones"), list):
        return [
            {
                "order": order,
                **{key: item[key] for key in sorted(item.keys() & allowed)},
            }
            for order, item in enumerate(zones, start=1)
            if isinstance(item, dict)
        ]
    return f"response received; decoded keys={sorted(value)}"


async def _get_queue_page(controller: AsyncRainbirdController, page: int) -> dict[str, Any]:
    """Contain the untyped upstream queue-command boundary."""
    result: dict[str, Any] = await controller._process_command(  # noqa: SLF001
        _queue_response,
        "CurrentQueueRequest",
        page,
    )
    return result


def _queue_response(response: dict[str, Any]) -> dict[str, Any]:
    """Return the already-decoded upstream queue response."""
    return response


async def _run(host: str, password: str) -> int:
    """Read local state one operation at a time without changing irrigation."""
    async with aiohttp.ClientSession() as session:
        try:
            controller, transport = await _connect(session, host, password)
        except RainbirdAuthException:
            print("Local authentication: FAILED (controller-local password/PIN rejected)")
            return 1
        except BaseException as error:
            print(f"Local authentication: NOT ESTABLISHED ({_safe_failure(error)})")
            return 1

        _transport_line(host, transport, "OK")
        print("Local authentication: OK")

        operations: list[tuple[str, Callable[[], Awaitable[Any]], Callable[[Any], object]]] = [
            (
                "Local irrigation enabled flag",
                controller.get_current_irrigation,
                lambda value: value,
            ),
            (
                "Local active terminals",
                controller.get_zone_states,
                lambda value: sorted(value.active_set),
            ),
            (
                "Local available terminals",
                controller.get_available_stations,
                lambda value: sorted(value.active_set),
            ),
            ("Local rain sensor active", controller.get_rain_sensor_state, lambda value: value),
            ("Local rain delay days", controller.get_rain_delay, lambda value: value),
        ]
        values: list[Any | None] = []
        for index, (label, request, render) in enumerate(operations):
            usable, value = await _read(label, request, render)
            values.append(value)
            if not usable:
                for skipped_label, _, _ in operations[index + 1 :]:
                    print(f"{skipped_label}: SKIPPED (connection unusable)")
                for page in (0, 1):
                    print(f"Local current queue page {page}: SKIPPED (connection unusable)")
                return 1

        queue_pages: list[dict[str, Any] | None] = []
        for page in (0, 1):
            _, value = await _read(
                f"Local current queue page {page}",
                partial(_get_queue_page, controller, page),
                _safe_queue,
            )
            queue_pages.append(value)
        queue = LocalQueueState.from_responses(*queue_pages)
        active_terminals = tuple(sorted(values[1].active_set)) if values[1] is not None else ()
        print(f"Local high-level irrigation state: {derive_run_state(active_terminals, queue)}")
    return 0


def main() -> None:
    """Run the local read-only diagnostic."""
    parser = argparse.ArgumentParser(
        description="Read IQ4 local live state without changing irrigation."
    )
    parser.add_argument("host", help="IQ4 controller hostname or IP address")
    parser.add_argument(
        "--show-auth-method",
        action="store_true",
        help="describe local authentication without displaying the credential",
    )
    args = parser.parse_args()
    if args.show_auth_method:
        print(f"Local authentication method: {AUTH_METHOD}")
    password = getpass.getpass("Local controller password: ")
    raise SystemExit(asyncio.run(_run(args.host, password)))


if __name__ == "__main__":
    main()
