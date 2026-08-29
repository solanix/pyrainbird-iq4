"""Data models for the Rain Bird IQ4 cloud API."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Literal


@dataclass(slots=True, frozen=True)
class Site:
    """An IQ4 site."""

    id: int
    company_id: int
    name: str
    description: str
    time_zone: str
    postal_code: str
    number_of_satellites: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Site:
        """Create a site from an IQ4 API response."""
        return cls(
            id=data["id"],
            company_id=data["companyId"],
            name=data["name"],
            description=data.get("description", ""),
            time_zone=data.get("timeZone", ""),
            postal_code=data.get("postalCode", ""),
            number_of_satellites=data.get("numberOfSatellites", 0),
        )


@dataclass(slots=True, frozen=True)
class Controller:
    """An IQ4 satellite/controller."""

    id: int
    name: str
    type: int
    site_id: int
    site_name: str
    station_count: int
    programs_used_count: int
    mac_address: str
    version: str
    is_mqtt: bool
    latitude: float
    longitude: float
    rain_delay: int
    simultaneous_stations: int
    is_shutdown: bool
    satellite_enabled: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Controller:
        """Create a controller from an IQ4 API response."""
        return cls(
            id=data["id"],
            name=data["name"],
            type=data.get("type", 0),
            site_id=data["siteId"],
            site_name=data.get("siteName", ""),
            station_count=data.get("stationCount", 0),
            programs_used_count=data.get("programsUsedCount", 0),
            mac_address=data.get("macAddress", ""),
            version=data.get("version", ""),
            is_mqtt=data.get("isMQTT", False),
            latitude=data.get("latitude", 0.0),
            longitude=data.get("longitude", 0.0),
            rain_delay=data.get("rainDelay", 0),
            simultaneous_stations=data.get("simultaneousStations", 0),
            is_shutdown=data.get("isShutdown", False),
            satellite_enabled=data.get("satelliteEnabled", False),
        )


@dataclass(slots=True, frozen=True)
class Station:
    """An irrigation station on an IQ4 controller."""

    id: int
    controller_id: int
    name: str
    terminal: int

    @classmethod
    def from_dict(cls, data: dict[str, Any], controller_id: int) -> Station:
        """Create a station from an IQ4 API response."""
        return cls(
            id=data["id"],
            controller_id=controller_id,
            name=data["name"],
            terminal=data.get("terminal", 0),
        )


@dataclass(slots=True, frozen=True)
class Program:
    """An irrigation program on an IQ4 controller."""

    id: int
    name: str
    short_name: str
    controller_id: int
    is_enabled: bool
    seasonal_adjustment: int
    week_days: str
    skip_days: int
    number: int

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Program:
        """Create a program from an IQ4 API response."""
        return cls(
            id=data["id"],
            name=data["name"],
            short_name=data.get("shortName", ""),
            controller_id=data["satelliteId"],
            is_enabled=data.get("isEnabled", True),
            seasonal_adjustment=data.get("programAdjust", 100),
            week_days=data.get("weekDays", "0000000"),
            skip_days=data.get("skipDays", 0),
            number=data.get("number", 1),
        )


@dataclass(slots=True, frozen=True)
class ScheduledTime:
    """A scheduled start time from the MQTT program endpoint."""

    time_of_day: time
    start_time_index: int
    raw_start_time: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduledTime:
        """Create a scheduled time from an IQ4 API response."""
        raw_start_time = data.get("startTime")
        time_of_day = time.min
        if isinstance(raw_start_time, str) and raw_start_time:
            with suppress(ValueError):
                time_of_day = (
                    datetime.fromisoformat(raw_start_time).time()
                    if "T" in raw_start_time
                    else time.fromisoformat(raw_start_time)
                )
        return cls(
            time_of_day=time_of_day,
            start_time_index=data.get("startTimeIndex", 0),
            raw_start_time=raw_start_time if isinstance(raw_start_time, str) else None,
        )


@dataclass(slots=True, frozen=True)
class ScheduledProgram:
    """A program and its scheduled start times from an MQTT controller."""

    program_id: int
    program_name: str
    scheduled_times: tuple[ScheduledTime, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ScheduledProgram:
        """Create a scheduled program from an IQ4 API response."""
        scheduled_times = data.get("scheduledTimes", [])
        if not isinstance(scheduled_times, list):
            scheduled_times = []
        return cls(
            program_id=data["programId"],
            program_name=data.get("programName", ""),
            scheduled_times=tuple(
                ScheduledTime.from_dict(item)
                for item in scheduled_times
                if isinstance(item, dict)
            ),
        )


@dataclass(slots=True, frozen=True)
class EffectiveProgram:
    """A controller program with the source of its catalog entry."""

    source: Literal["direct", "scheduled"]
    program: Program | ScheduledProgram


@dataclass(slots=True, frozen=True)
class StartTime:
    """A program start time in local controller time."""

    id: int
    program_id: int
    time_of_day: str
    enabled: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StartTime:
        """Create a start time from an IQ4 API response."""
        date_time = data.get(
            "dateTime",
            data.get("dateTimeLocal", "1999-09-09T00:00:00"),
        )
        time_of_day = date_time[11:16] if len(date_time) >= 16 else "00:00"
        return cls(
            id=data["id"],
            program_id=data["programId"],
            time_of_day=time_of_day,
            enabled=data.get("enabled", True),
        )


@dataclass(slots=True, frozen=True)
class RuntimeAssignment:
    """A program runtime assigned to a station."""

    program_step_id: int
    program_id: int
    program_short_name: str
    adjusted_run_time: str
    base_run_time: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RuntimeAssignment:
        """Create a runtime assignment from an IQ4 API response."""
        return cls(
            program_step_id=data["programStepId"],
            program_id=data["programId"],
            program_short_name=data.get("programShortName", ""),
            adjusted_run_time=data.get("adjustedRunTime", "00:00:00"),
            base_run_time=data.get("baseRunTime", "00:00:00"),
        )


@dataclass(slots=True, frozen=True)
class StationRuntime:
    """Runtime assignments grouped for one station."""

    station_id: int
    assignments: tuple[RuntimeAssignment, ...]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StationRuntime:
        """Create station runtimes from an IQ4 API response."""
        assignments = tuple(
            RuntimeAssignment.from_dict(item)
            for item in data.get("runtimeProgramAssignedList", [])
        )
        return cls(station_id=data["stationId"], assignments=assignments)


@dataclass(slots=True, frozen=True)
class ConnectionStatus:
    """The current MQTT connection status of a controller."""

    controller_id: int
    is_connected: bool
    timestamp: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConnectionStatus:
        """Create a connection status from an IQ4 API response."""
        return cls(
            controller_id=data["id"],
            is_connected=data.get("isConnected", False),
            timestamp=data.get("timestamp", ""),
        )
