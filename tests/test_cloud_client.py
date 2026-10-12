"""Tests for read-only IQ4 cloud API methods."""

from datetime import time
from typing import Any

import pytest

from pyrainbird_iq4.cloud.client import RainBirdIQ4CloudClient
from pyrainbird_iq4.cloud.models import ScheduledProgram, ScheduledTime
from pyrainbird_iq4.exceptions import (
    RainBirdIQ4APIError,
    RainBirdIQ4AuthenticationError,
    RainBirdIQ4Error,
    RainBirdIQ4ForbiddenError,
    RainBirdIQ4UnsupportedEndpointError,
)


class _HttpResponseContext:
    def __init__(self, response: "_HttpResponse") -> None:
        self.response = response

    async def __aenter__(self) -> "_HttpResponse":
        return self.response

    async def __aexit__(self, *_args: object) -> None:
        return None


class _HttpResponse:
    def __init__(self, body: str, content_type: str | None, status: int = 200) -> None:
        self.status = status
        self.headers = {"Content-Type": content_type} if content_type else {}
        self.body = body

    async def text(self) -> str:
        return self.body


class _HttpSession:
    def __init__(self, response: _HttpResponse) -> None:
        self.response = response

    def get(self, _url: str, **_kwargs: Any) -> _HttpResponseContext:
        return _HttpResponseContext(self.response)


class _FakeCloudClient(RainBirdIQ4CloudClient):
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, Any]] = []

    async def _get(self, path: str, *, params: Any = None) -> Any:
        self.calls.append((path, params))
        return self.responses[path]


async def test_read_only_cloud_methods_parse_iq4_data() -> None:
    """Read-only methods pass IQ4 parameters and parse sample responses."""
    client = _FakeCloudClient(
        {
            "/Station/GetStationListForSatellite": [
                {"id": 1, "name": "Front lawn", "terminal": 2, "isLocked": True}
            ],
            "/Program/GetProgramList": [
                {
                    "id": 10,
                    "name": "Morning",
                    "shortName": "A",
                    "satelliteId": 7,
                }
            ],
            "/Program/GetProgramListForMultiSites": {
                "programs": [
                    {"id": 11, "name": "Evening", "satelliteId": 7},
                    {"id": 12, "name": "Legacy", "satelliteId": 8},
                ]
            },
            "/Program/GetScheduledStartTimes": {"scheduled": [{"programId": 10, "time": "06:30"}]},
            "/StartTime/GetAllStartTimes": [
                {"id": 20, "programId": 10, "dateTime": "1999-09-09T06:30:00"},
                {"id": 21, "programId": 99, "dateTime": "1999-09-09T07:00:00"},
            ],
            "/ProgramStep/GetProgramsAssignedAndRunTimeBySatelliteId": [
                {
                    "stationId": 1,
                    "runtimeProgramAssignedList": [
                        {
                            "programStepId": 30,
                            "programId": 10,
                            "programShortName": "A",
                            "adjustedRunTime": "00:12:00",
                            "baseRunTime": "00:10:00",
                        }
                    ],
                }
            ],
            "/Satellite/isConnected": {
                "satellites": [{"id": 7, "isConnected": True, "timestamp": "now"}]
            },
        }
    )

    stations = await client.get_stations(7)
    programs = await client.get_programs(7)
    start_times = await client.get_start_times(7)
    runtimes = await client.get_station_runtimes(7)
    statuses = await client.get_connection_statuses([7, 8])
    all_programs = await client.get_all_programs()
    scheduled = await client.get_scheduled_start_times(7)

    assert stations[0].controller_id == 7
    assert stations[0].name == "Front lawn"
    assert stations[0].is_locked is True
    assert stations[0].is_enabled is True
    assert programs[0].short_name == "A"
    assert start_times[0].time_of_day == "06:30"
    assert len(start_times) == 1
    assert runtimes[0].assignments[0].adjusted_run_time == "00:12:00"
    assert statuses[0].is_connected is True
    assert [program.controller_id for program in all_programs] == [7, 8]
    assert scheduled == []
    assert client.calls == [
        ("/Station/GetStationListForSatellite", {"satelliteId": "7"}),
        ("/Program/GetProgramList", {"satelliteId": "7"}),
        ("/Program/GetProgramList", {"satelliteId": "7"}),
        (
            "/StartTime/GetAllStartTimes",
            {"includeProgram": "false", "includeProgramGroup": "false"},
        ),
        (
            "/ProgramStep/GetProgramsAssignedAndRunTimeBySatelliteId",
            {"satelliteId": "7"},
        ),
        (
            "/Satellite/isConnected",
            [("satelliteIds", "7"), ("satelliteIds", "8")],
        ),
        ("/Program/GetProgramListForMultiSites", None),
        ("/Program/GetScheduledStartTimes", {"satelliteId": "7"}),
    ]


async def test_connection_statuses_empty_input_does_not_request() -> None:
    """An empty controller list avoids an unnecessary API request."""
    client = _FakeCloudClient({})

    assert await client.get_connection_statuses([]) == []
    assert client.calls == []


async def test_program_wrapper_and_structure_are_supported() -> None:
    """Program responses may be wrapped instead of returned as a bare list."""
    client = _FakeCloudClient(
        {
            "/Program/GetProgramList": {
                "data": [
                    {
                        "id": 10,
                        "name": "Morning",
                        "satelliteId": 7,
                    }
                ],
                "meta": {"source": "cloud"},
            }
        }
    )

    programs = await client.get_programs(7)
    structure = await client.get_program_response_structure(7)

    assert programs[0].name == "Morning"
    assert "keys=[data, meta]" in structure
    assert "list[1]" in structure
    assert "Morning" not in structure


async def test_empty_program_response_is_supported() -> None:
    """A successful null program response is treated as an empty result."""
    client = _FakeCloudClient({"/Program/GetProgramList": None})

    assert await client.get_programs(1001) == []


async def test_scheduled_programs_parse_multiple_times_and_optional_fields() -> None:
    """Scheduled programs preserve multiple times and default missing fields."""
    client = _FakeCloudClient(
        {
            "/Program/GetScheduledStartTimes": [
                {
                    "programId": 10,
                    "programName": "Program A",
                    "scheduledTimes": [
                        {"startTime": "06:00", "startTimeIndex": 0},
                        {"startTime": "18:30", "startTimeIndex": 1},
                    ],
                },
                {"programId": 11, "scheduledTimes": None},
                {"programName": "missing id"},
            ]
        }
    )

    programs = await client.get_scheduled_start_times(1001)

    assert programs == [
        ScheduledProgram(
            10,
            "Program A",
            (
                ScheduledTime(time(6), 0, "06:00"),
                ScheduledTime(time(18, 30), 1, "18:30"),
            ),
        ),
        ScheduledProgram(11, "", ()),
    ]


async def test_effective_programs_use_direct_programs_first() -> None:
    """Legacy direct programs take precedence over scheduled fallback."""
    client = _FakeCloudClient(
        {
            "/Program/GetProgramList": [{"id": 1, "name": "Legacy A", "satelliteId": 7}],
            "/Program/GetScheduledStartTimes": [{"programId": 2, "programName": "MQTT A"}],
        }
    )

    effective = await client.get_effective_programs(7)

    assert len(effective) == 1
    assert effective[0].source == "direct"
    assert effective[0].program.name == "Legacy A"  # type: ignore[union-attr]


async def test_effective_programs_fall_back_to_scheduled_programs() -> None:
    """Empty direct programs use typed scheduled programs for MQTT controllers."""
    client = _FakeCloudClient(
        {
            "/Program/GetProgramList": [],
            "/Program/GetScheduledStartTimes": [{"programId": 2, "programName": "Program A"}],
        }
    )

    effective = await client.get_effective_programs(1001)

    assert len(effective) == 1
    assert effective[0].source == "scheduled"
    assert effective[0].program.program_id == 2  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "content_type",
    ["application/json", None, "text/plain"],
)
async def test_program_json_is_parsed_regardless_of_content_type(
    content_type: str | None,
) -> None:
    """Program JSON is accepted even when the server sends an incorrect MIME type."""
    session = _HttpSession(
        _HttpResponse(
            '[{"id": 10, "name": "Morning", "satelliteId": 7}]',
            content_type,
        )
    )
    client = RainBirdIQ4CloudClient("test-token", session=session)  # type: ignore[arg-type]

    programs = await client.get_all_programs()

    assert programs[0].name == "Morning"


async def test_empty_json_response_returns_empty_list() -> None:
    """An empty successful body becomes an empty result for list endpoints."""
    client = RainBirdIQ4CloudClient(
        "test-token",
        session=_HttpSession(_HttpResponse("", "text/plain")),  # type: ignore[arg-type]
    )

    assert await client.get_all_programs() == []


async def test_malformed_json_error_excludes_response_body() -> None:
    """Malformed JSON reports safe metadata without including the body."""
    client = RainBirdIQ4CloudClient(
        "test-token",
        session=_HttpSession(  # type: ignore[arg-type]
            _HttpResponse("username=password-and-secret", "text/plain")
        ),
    )

    with pytest.raises(
        RainBirdIQ4Error,
        match=(
            r"HTTP 200.*Program/GetProgramListForMultiSites.*response length 28.*"
            r"content type text/plain"
        ),
    ) as error:
        await client.get_all_programs()

    assert "password-and-secret" not in str(error.value)


async def test_response_debug_reports_structure_without_values() -> None:
    """Response debug metadata contains structure but no JSON values."""
    session = _HttpSession(
        _HttpResponse(
            '{"programs": [{"id": 10, "name": "Morning"}]}',
            "text/plain; charset=utf-8",
        )
    )
    client = RainBirdIQ4CloudClient("test-token", session=session)  # type: ignore[arg-type]

    summary = await client.get_response_debug("/Program/GetProgramListForMultiSites")

    assert summary == {
        "status": 200,
        "content_type": "text/plain",
        "content_length": 45,
        "empty": False,
        "json_structure": (
            "object keys=[programs] children=[programs=list[1] item=object keys=[id, name]]"
        ),
    }


async def test_controller_detail_structure_is_value_free() -> None:
    """Detailed controller diagnostics expose keys and types, not values."""
    client = _FakeCloudClient(
        {
            "/Satellite/GetSatellite": {
                "id": 1001,
                "name": "Example Site",
                "state": {"activeStationId": None, "remainingRuntime": 0},
            },
            "/Station/GetStation": {
                "id": 101,
                "name": "Station 001",
                "enabled": True,
            },
        }
    )

    structure = await client.get_controller_detail_response_structure(1001)
    station_structure = await client.get_station_detail_response_structure(101)

    assert structure == (
        "object keys=[id, name, state] children=[state=object "
        "keys=[activeStationId, remainingRuntime] "
        "children=[activeStationId=null]]"
    )
    assert "Example Site" not in structure
    assert station_structure == "object keys=[enabled, id, name]"
    assert "Station 001" not in station_structure
    assert client.calls == [
        ("/Satellite/GetSatellite", {"satelliteId": "1001"}),
        ("/Station/GetStation", {"stationId": "101"}),
    ]


async def test_station_list_debug_allowlists_configuration_values() -> None:
    """Station-list debug reveals only the requested candidate state fields."""
    client = _FakeCloudClient(
        {
            "/Station/GetStationListForSatellite": [
                {
                    "id": 108,
                    "name": "Secret back garden",
                    "terminal": 8,
                    "suspended": True,
                    "isLocked": False,
                    "discoveryState": "Discovered",
                    "status": None,
                    "notes": "private note",
                    "coordinates": "1,2",
                    "address": "private address",
                    "credential": "do-not-print",
                },
                {"id": 101, "terminal": 1, "name": "Secret front garden"},
            ]
        }
    )

    lines = await client.get_station_list_debug(1001)

    assert lines == [
        "Station list item 1: "
        "id=108 terminal=8 suspended=True isLocked=False "
        "discoveryState=\x27Discovered\x27 status=None",
        "Station list item 2: id=101 terminal=1 suspended=None isLocked=None "
        "discoveryState=None status=None",
    ]
    output = "\n".join(lines)
    for sensitive_value in (
        "Secret back garden",
        "Secret front garden",
        "private note",
        "1,2",
        "private address",
        "do-not-print",
    ):
        assert sensitive_value not in output
    assert client.calls == [("/Station/GetStationListForSatellite", {"satelliteId": "1001"})]


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        pytest.param(403, RainBirdIQ4ForbiddenError, id="forbidden"),
        pytest.param(404, RainBirdIQ4UnsupportedEndpointError, id="unsupported"),
        pytest.param(500, RainBirdIQ4APIError, id="api-error"),
    ],
)
async def test_endpoint_errors_are_classified_without_secrets(
    status: int,
    error_type: type[RainBirdIQ4APIError],
) -> None:
    """Endpoint errors include only a sanitized path and HTTP status."""
    secret = "secret-access-token"
    session = _HttpSession(
        _HttpResponse(
            "authorization=Bearer leaked-value cookie=session-secret",
            "application/json",
            status,
        )
    )
    client = RainBirdIQ4CloudClient(secret, session=session)

    with pytest.raises(error_type) as raised:
        await client._get("/Satellite/GetSatellite?access_token=query-secret#fragment-secret")

    assert raised.value.endpoint == "/Satellite/GetSatellite"
    assert raised.value.status == status
    assert secret not in str(raised.value)
    assert "query-secret" not in str(raised.value)
    assert "fragment-secret" not in str(raised.value)
    assert "leaked-value" not in str(raised.value)
    assert "session-secret" not in str(raised.value)


async def test_unauthorized_endpoint_is_token_failure() -> None:
    """HTTP 401 remains an access-token authentication failure."""
    session = _HttpSession(_HttpResponse("", "application/json", 401))
    client = RainBirdIQ4CloudClient("secret-access-token", session=session)

    with pytest.raises(
        RainBirdIQ4AuthenticationError,
        match="access token was rejected by endpoint /Site/GetSites",
    ):
        await client.get_sites()
