"""Asynchronous client for the Rain Bird IQ4 cloud API."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import aiohttp

from ..exceptions import (
    RainBirdIQ4AuthenticationError,
    RainBirdIQ4ConnectionError,
    RainBirdIQ4Error,
    safe_error_detail,
)
from .models import (
    ConnectionStatus,
    Controller,
    EffectiveProgram,
    Program,
    ScheduledProgram,
    Site,
    StartTime,
    Station,
    StationRuntime,
)

API_BASE = "https://iq4server.rainbird.com/coreapi/api"


def _safe_content_type(value: str | None) -> str:
    """Return only the media type portion of a response Content-Type header."""
    return (value or "").split(";", 1)[0].strip()[:100]


def _record_list(data: Any, wrapper_keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """Extract a list of JSON records from a list or common API wrapper."""
    if data is None:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in wrapper_keys:
            if key in data:
                return _record_list(data[key], wrapper_keys)
    return []


def _response_structure(data: Any) -> str:
    """Describe JSON containers without exposing response values."""
    if data is None:
        return "null"
    if isinstance(data, list):
        if not data:
            return "list[0]"
        return f"list[{len(data)}] item={_response_structure(data[0])}"
    if isinstance(data, dict):
        keys = ", ".join(sorted(str(key) for key in data))
        children = ", ".join(
            f"{key}={_response_structure(data[key])}"
            for key in sorted(data)
            if isinstance(data[key], (dict, list)) or data[key] is None
        )
        return f"object keys=[{keys}]" + (f" children=[{children}]" if children else "")
    return type(data).__name__


class RainBirdIQ4CloudClient:
    """Client for the Rain Bird IQ4 cloud API."""

    def __init__(
        self,
        token: str,
        *,
        session: aiohttp.ClientSession | None = None,
        timeout: float = 15.0,
    ) -> None:
        """Initialize the cloud client."""
        self._token = token
        self._session = session
        self._timeout = timeout

    async def _get(
        self,
        path: str,
        *,
        params: Mapping[str, str] | list[tuple[str, str]] | None = None,
    ) -> Any:
        """Perform an authenticated GET request."""
        url = f"{API_BASE}{path}"

        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

        timeout = aiohttp.ClientTimeout(total=self._timeout)

        own_session = self._session is None
        session = self._session or aiohttp.ClientSession(timeout=timeout)

        try:
            async with session.get(url, headers=headers, params=params) as response:
                if response.status in (401, 403):
                    raise RainBirdIQ4AuthenticationError(
                        f"IQ4 authentication failed with HTTP {response.status}"
                    )

                if response.status >= 400:
                    raise RainBirdIQ4Error(
                        f"IQ4 API returned HTTP {response.status}"
                    )

                content_type = _safe_content_type(response.headers.get("Content-Type"))
                body = await response.text()
                if not body.strip():
                    return []
                try:
                    return json.loads(body)
                except json.JSONDecodeError as exc:
                    raise RainBirdIQ4Error(
                        "IQ4 API returned invalid JSON: "
                        f"HTTP {response.status}, endpoint {path}, "
                        f"response length {len(body)}, "
                        f"content type {content_type or '<missing>'}"
                    ) from exc

        except RainBirdIQ4Error:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise RainBirdIQ4ConnectionError(
                "IQ4 connection failed: "
                f"{type(exc).__name__}: {safe_error_detail(exc)}"
            ) from exc
        finally:
            if own_session:
                await session.close()

    async def get_response_debug(
        self,
        path: str,
        *,
        params: Mapping[str, str] | list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        """Return safe metadata about one JSON API response."""
        url = f"{API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        own_session = self._session is None
        session = self._session or aiohttp.ClientSession(timeout=timeout)

        try:
            async with session.get(url, headers=headers, params=params) as response:
                body = await response.text()
                summary: dict[str, Any] = {
                    "status": response.status,
                    "content_type": _safe_content_type(
                        response.headers.get("Content-Type")
                    ) or None,
                    "content_length": len(body),
                    "empty": not body.strip(),
                    "json_structure": None,
                }
                if body.strip():
                    try:
                        summary["json_structure"] = _response_structure(json.loads(body))
                    except json.JSONDecodeError:
                        summary["json_structure"] = "invalid-json"
                return summary
        except (aiohttp.ClientError, TimeoutError, OSError) as exc:
            raise RainBirdIQ4ConnectionError(
                "IQ4 connection failed: "
                f"{type(exc).__name__}: {safe_error_detail(exc)}"
            ) from exc
        finally:
            if own_session:
                await session.close()

    async def get_sites(self) -> list[Site]:
        """Return sites available to the authenticated account."""
        data = await self._get("/Site/GetSites")
        return [Site.from_dict(item) for item in data]

    async def get_controllers(self) -> list[Controller]:
        """Return IQ4 controllers available to the authenticated account."""
        data = await self._get("/Satellite/GetSatelliteList")
        return [Controller.from_dict(item) for item in data]

    async def get_stations(self, controller_id: int) -> list[Station]:
        """Return stations for a controller."""
        data = await self._get(
            "/Station/GetStationListForSatellite",
            params={"satelliteId": str(controller_id)},
        )
        return [Station.from_dict(item, controller_id) for item in data]

    async def get_programs(self, controller_id: int) -> list[Program]:
        """Return programs for a controller."""
        data = await self._get(
            "/Program/GetProgramList",
            params={"satelliteId": str(controller_id)},
        )
        records = _record_list(
            data,
            ("programs", "programList", "data", "items", "results"),
        )
        return [Program.from_dict(item) for item in records]

    async def get_program_response_structure(self, controller_id: int) -> str:
        """Return a value-free description of the program API response."""
        data = await self._get(
            "/Program/GetProgramList",
            params={"satelliteId": str(controller_id)},
        )
        return _response_structure(data)

    async def get_all_programs(self) -> list[Program]:
        """Return programs from the multi-site IQ4 endpoint."""
        data = await self._get("/Program/GetProgramListForMultiSites")
        records = _record_list(
            data,
            ("programs", "programList", "data", "items", "results"),
        )
        return [Program.from_dict(item) for item in records]

    async def get_scheduled_start_times(
        self,
        satellite_id: int,
    ) -> list[ScheduledProgram]:
        """Return typed scheduled programs for a controller."""
        data = await self._get(
            "/Program/GetScheduledStartTimes",
            params={"satelliteId": str(satellite_id)},
        )
        records = _record_list(
            data,
            ("scheduledPrograms", "programs", "data", "items", "results"),
        )
        return [
            ScheduledProgram.from_dict(item)
            for item in records
            if "programId" in item
        ]

    async def get_effective_programs(self, controller_id: int) -> list[EffectiveProgram]:
        """Return direct programs or schedule-derived MQTT programs."""
        programs = await self.get_programs(controller_id)
        if programs:
            return [EffectiveProgram("direct", program) for program in programs]
        scheduled_programs = await self.get_scheduled_start_times(controller_id)
        return [EffectiveProgram("scheduled", program) for program in scheduled_programs]

    async def get_scheduled_start_time_response_structure(self, satellite_id: int) -> str:
        """Return a value-free description of scheduled start times."""
        return (
            await self.get_response_debug(
                "/Program/GetScheduledStartTimes",
                params={"satelliteId": str(satellite_id)},
            )
        )["json_structure"] or "null"

    async def get_start_times(self, controller_id: int) -> list[StartTime]:
        """Return start times for programs belonging to a controller."""
        programs = await self.get_programs(controller_id)
        program_ids = {program.id for program in programs}
        data = await self._get(
            "/StartTime/GetAllStartTimes",
            params={
                "includeProgram": "false",
                "includeProgramGroup": "false",
            },
        )
        return [
            StartTime.from_dict(item)
            for item in data
            if item.get("programId") in program_ids
        ]

    async def get_station_runtimes(self, controller_id: int) -> list[StationRuntime]:
        """Return runtime assignments grouped by station."""
        data = await self._get(
            "/ProgramStep/GetProgramsAssignedAndRunTimeBySatelliteId",
            params={"satelliteId": str(controller_id)},
        )
        return [StationRuntime.from_dict(item) for item in data]

    async def get_connection_statuses(
        self,
        controller_ids: list[int],
    ) -> list[ConnectionStatus]:
        """Return connection statuses for one or more controllers."""
        if not controller_ids:
            return []
        params = [("satelliteIds", str(controller_id)) for controller_id in controller_ids]
        data = await self._get("/Satellite/isConnected", params=params)
        statuses = data.get("satellites", data) if isinstance(data, dict) else data
        return [ConnectionStatus.from_dict(item) for item in statuses]
