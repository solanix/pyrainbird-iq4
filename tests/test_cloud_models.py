"""Tests for IQ4 cloud models."""

from datetime import time

from pyrainbird_iq4.cloud.models import (
    Controller,
    ScheduledTime,
    Site,
    Station,
    disabled_stations,
    enabled_stations,
    unknown_state_stations,
)


def test_site_from_dict() -> None:
    """Test parsing an IQ4 site."""
    site = Site.from_dict(
        {
            "id": 10,
            "companyId": 20,
            "name": "Test Site",
            "description": "Test irrigation site",
            "timeZone": "Central Standard Time",
            "postalCode": "73505",
            "numberOfSatellites": 1,
        }
    )

    assert site.id == 10
    assert site.company_id == 20
    assert site.name == "Test Site"
    assert site.number_of_satellites == 1


def test_controller_from_dict() -> None:
    """Test parsing an IQ4 controller."""
    controller = Controller.from_dict(
        {
            "id": 100,
            "name": "Test Controller",
            "type": 1,
            "siteId": 10,
            "siteName": "Test Site",
            "stationCount": 7,
            "programsUsedCount": 2,
            "macAddress": "00:11:22:33:44:55",
            "version": "1.0",
            "isMQTT": True,
            "latitude": 0.0,
            "longitude": 0.0,
            "rainDelay": 0,
            "simultaneousStations": 1,
            "isShutdown": False,
            "satelliteEnabled": True,
        }
    )

    assert controller.id == 100
    assert controller.station_count == 7
    assert controller.programs_used_count == 2
    assert controller.is_mqtt is True
    assert controller.satellite_enabled is True


def test_station_preserves_cloud_name_and_immutable_identity() -> None:
    """Cloud names are presentation data independent of stable station identity."""
    station = Station.from_dict({"id": 108, "name": "Front Lawn", "terminal": 8}, 1001)

    assert (station.id, station.controller_id, station.terminal) == (108, 1001, 8)
    assert station.name == "Front Lawn"


def test_station_blank_name_uses_padded_terminal_fallback() -> None:
    """Null, blank, and whitespace-only API names use the deterministic fallback."""
    for name in (None, "", "   "):
        station = Station.from_dict({"id": 108, "name": name, "terminal": 8}, 1001)
        assert station.name == "Station 008"


def test_station_enabled_state_uses_verified_is_locked_semantics() -> None:
    """The raw lock field maps true/false/missing without collapsing unknown."""
    stations = [
        Station.from_dict(
            {
                "id": terminal,
                "name": f"Cloud Station {terminal}",
                "terminal": terminal,
                "isLocked": terminal <= 7,
            },
            1001,
        )
        for terminal in range(1, 11)
    ]

    assert [station.terminal for station in enabled_stations(stations)] == list(range(1, 8))
    assert [station.terminal for station in disabled_stations(stations)] == [8, 9, 10]
    assert unknown_state_stations(stations) == []
    assert stations[0].is_locked is True
    assert stations[7].is_locked is False
    assert stations[9].is_locked is False
    unknown = Station.from_dict({"id": 11, "name": "Spare", "terminal": 11}, 1001)
    assert unknown.is_locked is None
    assert unknown.is_enabled is None
    assert unknown_state_stations([unknown]) == [unknown]
    assert stations[7].name == "Cloud Station 8"


def test_scheduled_time_parses_dummy_date_as_time_of_day() -> None:
    """The API's dummy date is excluded from the public time value."""
    scheduled_time = ScheduledTime.from_dict(
        {"startTime": "1999-09-09T05:00:00", "startTimeIndex": 0}
    )

    assert scheduled_time.time_of_day == time(5)
    assert scheduled_time.time_of_day.strftime("%H:%M") == "05:00"
    assert scheduled_time.raw_start_time == "1999-09-09T05:00:00"


def test_scheduled_time_preserves_seconds() -> None:
    """Seconds remain available in the typed time value when the API sends them."""
    scheduled_time = ScheduledTime.from_dict(
        {"startTime": "1999-09-09T19:40:12", "startTimeIndex": 1}
    )

    assert scheduled_time.time_of_day == time(19, 40, 12)
    assert scheduled_time.time_of_day.strftime("%H:%M") == "19:40"
    assert scheduled_time.raw_start_time == "1999-09-09T19:40:12"


def test_scheduled_time_defaults_missing_or_malformed_value() -> None:
    """Missing or malformed schedule values remain safely parseable."""
    missing = ScheduledTime.from_dict({})
    malformed = ScheduledTime.from_dict({"startTime": "not-a-time"})

    assert missing.time_of_day == time.min
    assert malformed.time_of_day == time.min
    assert malformed.raw_start_time == "not-a-time"


def test_controller_preserves_missing_rain_delay() -> None:
    """A missing rain-delay field is not fabricated as zero."""
    controller = Controller.from_dict(
        {
            "id": 100,
            "name": "Controller",
            "siteId": 1,
        }
    )

    assert controller.rain_delay is None
