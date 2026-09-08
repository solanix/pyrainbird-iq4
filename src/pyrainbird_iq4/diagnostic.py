"""Development diagnostic command for the Rain Bird IQ4 cloud service."""

from __future__ import annotations

import argparse
import asyncio
import getpass
from collections.abc import Awaitable, Callable
from functools import partial

from .cloud.auth import authenticate
from .cloud.client import RainBirdIQ4CloudClient
from .cloud.models import (
    ConnectionStatus,
    Controller,
    EffectiveProgram,
    Program,
    Site,
    StartTime,
    Station,
    StationRuntime,
)
from .exceptions import (
    RainBirdIQ4APIError,
    RainBirdIQ4AuthenticationError,
    RainBirdIQ4ConnectionError,
    RainBirdIQ4Error,
    RainBirdIQ4ForbiddenError,
    RainBirdIQ4UnsupportedEndpointError,
    safe_error_detail,
)


def _print_site(site: Site) -> None:
    """Print non-secret details for one site."""
    print(f"Site: {site.name}")
    print(f"  ID: {site.id}")
    print(f"  Time zone: {site.time_zone}")
    print(f"  Number of controllers: {site.number_of_satellites}")


def _print_program_summary(label: str, programs: list[Program], controller_id: int) -> None:
    """Print safe program identifiers and names for one controller."""
    summary = ", ".join(f"{program.id}: {program.name}" for program in programs)
    print(f"{label} for {controller_id}: {summary or '(none)'}")


def _runtime_only_programs(
    runtimes: list[StationRuntime],
    effective_programs: list[EffectiveProgram],
) -> list[str]:
    """Return runtime program labels absent from the effective catalog."""
    known: set[str] = set()
    for entry in effective_programs:
        program = entry.program
        if isinstance(program, Program):
            known.update((program.name, program.short_name))
        else:
            known.add(program.program_name)
            if program.program_name.lower().startswith("program "):
                known.add(program.program_name[8:])

    references = {
        assignment.program_short_name
        for runtime in runtimes
        for assignment in runtime.assignments
        if assignment.program_short_name
    }
    return sorted(references - known)


def _print_controller(
    controller: Controller,
    stations: list[Station],
    effective_programs: list[EffectiveProgram],
    start_times: list[StartTime],
    runtimes: list[StationRuntime],
    connection_status: ConnectionStatus | None,
    connected: bool,
    runtime_only: list[str],
) -> None:
    """Print non-secret details for one controller."""
    state = "CONNECTED" if connected else "DISCONNECTED / STALE"
    print(f"Controller: {controller.name} [{state}]")
    print(f"  ID: {controller.id}")
    print(f"  Site name: {controller.site_name}")
    print(f"  Station count: {controller.station_count}")
    print(f"  Programs used count: {controller.programs_used_count}")
    print(f"  Firmware/version: {controller.version}")
    print(f"  MQTT: {controller.is_mqtt}")
    print(f"  Enabled: {controller.satellite_enabled}")
    print(f"  Connected: {connection_status.is_connected if connection_status else False}")
    print("  Stations:")
    for station in stations:
        print(f"    {station.id}: {station.name} (terminal {station.terminal})")
    print("  Programs:")
    for entry in effective_programs:
        program = entry.program
        if isinstance(program, Program):
            print(f"    {program.id}: {program.name} (enabled {program.is_enabled})")
            continue
        print(f"    {program.program_id}: {program.program_name}")
        print("      Start times:")
        for scheduled_time in program.scheduled_times:
            print(f"        {scheduled_time.time_of_day:%H:%M}")
    print("  Start times:")
    if effective_programs and effective_programs[0].source == "scheduled":
        print("    (shown with each program above)")
    else:
        for start_time in start_times:
            print(f"    Program {start_time.program_id}: {start_time.time_of_day}")
    print("  Station runtimes:")
    for runtime in runtimes:
        assignments = ", ".join(
            f"{assignment.program_short_name or assignment.program_id}: "
            f"{assignment.adjusted_run_time}"
            for assignment in runtime.assignments
        )
        print(f"    Station {runtime.station_id}: {assignments or 'none'}")
    if runtime_only:
        print("  Runtime-only program references:")
        for program_name in runtime_only:
            print(f"    {program_name}")


async def _print_optional_response_structure(
    label: str,
    identifier: int,
    request: Callable[[], Awaitable[str]],
) -> None:
    """Print one optional read-only response without aborting the diagnostic."""
    try:
        structure = await request()
    except RainBirdIQ4ForbiddenError as err:
        print(
            f"{label} response for {identifier}: FORBIDDEN "
            f"(HTTP {err.status}; endpoint {err.endpoint})"
        )
    except RainBirdIQ4UnsupportedEndpointError as err:
        print(
            f"{label} response for {identifier}: UNSUPPORTED "
            f"(HTTP {err.status}; endpoint {err.endpoint})"
        )
    except RainBirdIQ4APIError as err:
        print(
            f"{label} response for {identifier}: ERROR (HTTP {err.status}; endpoint {err.endpoint})"
        )
    else:
        print(f"{label} response structure for {identifier}: {structure}")


async def _run_diagnostic(
    username: str,
    password: str,
    controller_id: int | None = None,
    debug_program_response: bool = False,
    debug_controller_detail: bool = False,
    debug_station_list: bool = False,
) -> None:
    """Authenticate and print read-only IQ4 account information."""
    token = await authenticate(username, password)
    print("Authentication successful")

    client = RainBirdIQ4CloudClient(token)
    sites = await client.get_sites()
    controllers = await client.get_controllers()
    all_programs = await client.get_all_programs() if debug_program_response else []
    programs_by_controller: dict[int, list[Program]] = {}
    for program in all_programs:
        programs_by_controller.setdefault(program.controller_id, []).append(program)
    connection_statuses = await client.get_connection_statuses(
        [controller.id for controller in controllers]
    )
    status_by_controller = {status.controller_id: status for status in connection_statuses}

    if controller_id is not None:
        selected = [controller for controller in controllers if controller.id == controller_id]
        if not selected:
            raise RainBirdIQ4Error(f"Controller {controller_id} was not found")
        controllers = selected

    for site in sites:
        _print_site(site)

    for controller in controllers:
        connected = status_by_controller.get(
            controller.id, ConnectionStatus(controller.id, False, "")
        ).is_connected
        effective_programs = await client.get_effective_programs(controller.id)
        if debug_controller_detail:
            await _print_optional_response_structure(
                "Controller detail",
                controller.id,
                partial(client.get_controller_detail_response_structure, controller.id),
            )
        if debug_program_response:
            structure = await client.get_program_response_structure(controller.id)
            print(f"Program response structure for {controller.id}: {structure}")
            multi_site_programs = programs_by_controller.get(controller.id, [])
            direct_programs = [
                entry.program
                for entry in effective_programs
                if entry.source == "direct" and isinstance(entry.program, Program)
            ]
            _print_program_summary("Direct programs", direct_programs, controller.id)
            _print_program_summary("Multi-site programs", multi_site_programs, controller.id)
            scheduled_structure = await client.get_scheduled_start_time_response_structure(
                controller.id
            )
            print(
                f"Scheduled start-time response structure for {controller.id}: "
                f"{scheduled_structure}"
            )
        stations = await client.get_stations(controller.id)
        if debug_station_list:
            for line in await client.get_station_list_debug(controller.id):
                print(line)
        if debug_controller_detail and stations:
            station_id = stations[0].id
            await _print_optional_response_structure(
                "Station detail",
                station_id,
                partial(client.get_station_detail_response_structure, station_id),
            )
        start_times = (
            await client.get_start_times(controller.id)
            if not effective_programs or effective_programs[0].source == "direct"
            else []
        )
        runtimes = await client.get_station_runtimes(controller.id)
        runtime_only = _runtime_only_programs(runtimes, effective_programs)
        _print_controller(
            controller,
            stations,
            effective_programs,
            start_times,
            runtimes,
            status_by_controller.get(controller.id),
            connected,
            runtime_only,
        )


def main(argv: list[str] | None = None) -> None:
    """Run the interactive IQ4 diagnostic."""
    parser = argparse.ArgumentParser(description="Run a read-only IQ4 diagnostic")
    parser.add_argument(
        "--controller",
        "--controller-id",
        dest="controller_id",
        type=int,
        help="Only inspect this controller",
    )
    parser.add_argument(
        "--debug-program-response",
        "--program-debug",
        action="store_true",
        dest="debug_program_response",
        help="Print the value-free GetProgramList response structure",
    )
    parser.add_argument(
        "--debug-station-list",
        action="store_true",
        help="Print station-list keys and allowlisted configuration fields",
    )
    parser.add_argument(
        "--debug-controller-detail",
        "--live-state-debug",
        action="store_true",
        dest="debug_controller_detail",
        help="Print value-free controller and station detail structures",
    )
    args = parser.parse_args(argv)
    username = input("IQ4 username: ")
    password = getpass.getpass("IQ4 password: ")

    try:
        asyncio.run(
            _run_diagnostic(
                username,
                password,
                args.controller_id,
                args.debug_program_response,
                args.debug_controller_detail,
                **({"debug_station_list": True} if args.debug_station_list else {}),
            )
        )
    except RainBirdIQ4AuthenticationError as exc:
        print(f"Diagnostic error: IQ4 authentication failed: {safe_error_detail(exc)}")
    except RainBirdIQ4ForbiddenError as exc:
        print(f"Diagnostic error: IQ4 API endpoint forbidden: {exc.endpoint} (HTTP {exc.status})")
    except RainBirdIQ4UnsupportedEndpointError as exc:
        print(f"Diagnostic error: IQ4 API endpoint unsupported: {exc.endpoint} (HTTP {exc.status})")
    except RainBirdIQ4APIError as exc:
        print(f"Diagnostic error: IQ4 API endpoint failed: {exc.endpoint} (HTTP {exc.status})")
    except RainBirdIQ4ConnectionError as exc:
        print(f"Diagnostic error: {safe_error_detail(exc)}")
    except RainBirdIQ4Error as exc:
        print(f"Diagnostic error: IQ4 API request failed: {safe_error_detail(exc)}")


if __name__ == "__main__":
    main()
