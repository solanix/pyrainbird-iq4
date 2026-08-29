"""Tests for IQ4 cloud models."""

from datetime import time

from pyrainbird_iq4.cloud.models import Controller, ScheduledTime, Site


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
