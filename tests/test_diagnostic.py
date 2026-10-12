"""Tests for the IQ4 development diagnostic command."""

import pytest

from pyrainbird_iq4 import diagnostic
from pyrainbird_iq4.cloud.models import (
    ConnectionStatus,
    Controller,
    EffectiveProgram,
    Program,
    RuntimeAssignment,
    Site,
    StartTime,
    Station,
    StationRuntime,
)
from pyrainbird_iq4.exceptions import (
    RainBirdIQ4AuthenticationError,
    RainBirdIQ4ConnectionError,
    RainBirdIQ4ForbiddenError,
)


def test_diagnostic_prints_non_secret_cloud_data(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """The diagnostic prints results without exposing credentials or tokens."""
    username = "test-user"
    password = "test-password"
    access_token = "test-access-token"

    async def fake_authenticate(received_username: str, received_password: str) -> str:
        assert received_username == username
        assert received_password == password
        return access_token

    class FakeClient:
        def __init__(self, token: str) -> None:
            assert token == access_token

        async def get_sites(self) -> list[Site]:
            return [
                Site(
                    id=1,
                    company_id=2,
                    name="Test Site",
                    description="",
                    time_zone="UTC",
                    postal_code="",
                    number_of_satellites=1,
                )
            ]

        async def get_controllers(self) -> list[Controller]:
            return [
                Controller(
                    id=3,
                    name="Test Controller",
                    type=1,
                    site_id=1,
                    site_name="Test Site",
                    station_count=8,
                    programs_used_count=2,
                    mac_address="",
                    version="1.2.3",
                    is_mqtt=True,
                    latitude=0.0,
                    longitude=0.0,
                    rain_delay=0,
                    simultaneous_stations=1,
                    is_shutdown=False,
                    satellite_enabled=True,
                )
            ]

        async def get_connection_statuses(
            self,
            _controller_ids: list[int],
        ) -> list[ConnectionStatus]:
            return [ConnectionStatus(3, True, "2026-01-01T00:00:00Z")]

        async def get_controller_detail_response_structure(self, _controller_id: int) -> str:
            raise RainBirdIQ4ForbiddenError("/Satellite/GetSatellite")

        async def get_stations(self, _controller_id: int) -> list[Station]:
            return [Station(4, 3, "Front lawn", 1)]

        async def get_station_detail_response_structure(self, _station_id: int) -> str:
            return "object keys=[id, name]"

        async def get_programs(self, _controller_id: int) -> list[Program]:
            return [Program(5, "Morning", "A", 3, True, 100, "1111111", 0, 1)]

        async def get_effective_programs(
            self,
            controller_id: int,
        ) -> list[EffectiveProgram]:
            return [
                EffectiveProgram("direct", program)
                for program in await self.get_programs(controller_id)
            ]

        async def get_start_times(self, _controller_id: int) -> list[StartTime]:
            return [StartTime(6, 5, "06:30", True)]

        async def get_station_runtimes(self, _controller_id: int) -> list[StationRuntime]:
            return [
                StationRuntime(
                    4,
                    (
                        RuntimeAssignment(7, 5, "A", "00:10:00", "00:10:00"),
                        RuntimeAssignment(8, 6, "C", "00:05:00", "00:05:00"),
                    ),
                )
            ]

    monkeypatch.setattr(diagnostic, "authenticate", fake_authenticate)
    monkeypatch.setattr(diagnostic, "RainBirdIQ4CloudClient", FakeClient)
    monkeypatch.setattr("builtins.input", lambda _prompt: username)
    monkeypatch.setattr(diagnostic.getpass, "getpass", lambda _prompt: password)

    diagnostic.main(["--live-state-debug"])

    output = capsys.readouterr().out
    assert "Authentication successful" in output
    assert "Test Site" in output
    assert "Test Controller" in output
    assert "UTC" in output
    assert "Front lawn" in output
    assert "Morning" in output
    assert "06:30" in output
    assert "Connected: True" in output
    assert (
        "Controller detail response for 3: FORBIDDEN (HTTP 403; endpoint /Satellite/GetSatellite)"
    ) in output
    assert "Station detail response structure for 4" in output
    assert "Runtime-only program references:" in output
    assert "    C" in output
    assert "test-password" not in output
    assert access_token not in output


def test_diagnostic_reports_authentication_error(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """Authentication failures are concise and traceback-free."""

    async def fake_run(
        _username: str,
        _password: str,
        _controller_id: int | None = None,
        _debug_program_response: bool = False,
        _debug_controller_detail: bool = False,
    ) -> None:
        raise RainBirdIQ4AuthenticationError("details are not displayed")

    monkeypatch.setattr(diagnostic, "_run_diagnostic", fake_run)
    monkeypatch.setattr("builtins.input", lambda _prompt: "test-user")
    monkeypatch.setattr(diagnostic.getpass, "getpass", lambda _prompt: "test-password")

    diagnostic.main([])

    output = capsys.readouterr().out
    assert output.endswith(
        "Diagnostic error: IQ4 authentication failed: details are not displayed\n"
    )
    assert "Traceback" not in output


def test_diagnostic_redacts_sensitive_error_details(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    """Diagnostic error output redacts credential-like values."""

    async def fake_run(
        _username: str,
        _password: str,
        _controller_id: int | None = None,
        _debug_program_response: bool = False,
        _debug_controller_detail: bool = False,
    ) -> None:
        raise RainBirdIQ4ConnectionError(
            "ClientConnectorError: https://example.test/#access_token=secret-token"
        )

    monkeypatch.setattr(diagnostic, "_run_diagnostic", fake_run)
    monkeypatch.setattr("builtins.input", lambda _prompt: "test-user")
    monkeypatch.setattr(diagnostic.getpass, "getpass", lambda _prompt: "test-password")

    diagnostic.main([])

    output = capsys.readouterr().out
    assert "ClientConnectorError" in output
    assert "access_token=<redacted>" in output
    assert "secret-token" not in output
    assert "test-password" not in output


def test_diagnostic_accepts_controller_and_program_debug_options(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The live diagnostic options are passed without exposing credentials."""
    received: dict[str, object] = {}

    async def fake_run(
        _username: str,
        _password: str,
        controller_id: int | None = None,
        debug_program_response: bool = False,
        debug_controller_detail: bool = False,
    ) -> None:
        received["controller_id"] = controller_id
        received["program_debug"] = debug_program_response
        received["controller_detail_debug"] = debug_controller_detail

    monkeypatch.setattr(diagnostic, "_run_diagnostic", fake_run)
    monkeypatch.setattr("builtins.input", lambda _prompt: "test-user")
    monkeypatch.setattr(diagnostic.getpass, "getpass", lambda _prompt: "test-password")

    diagnostic.main(["--controller", "1001", "--program-debug", "--live-state-debug"])

    assert received == {
        "controller_id": 1001,
        "program_debug": True,
        "controller_detail_debug": True,
    }


def test_diagnostic_accepts_station_list_debug_option(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The safe station-list option reaches the read-only runner."""
    received: dict[str, object] = {}

    async def fake_run(
        _username: str,
        _password: str,
        _controller_id: int | None = None,
        _debug_program_response: bool = False,
        _debug_controller_detail: bool = False,
        debug_station_list: bool = False,
    ) -> None:
        received["station_list_debug"] = debug_station_list

    monkeypatch.setattr(diagnostic, "_run_diagnostic", fake_run)
    monkeypatch.setattr("builtins.input", lambda _prompt: "test-user")
    monkeypatch.setattr(diagnostic.getpass, "getpass", lambda _prompt: "test-password")

    diagnostic.main(["--controller", "1001", "--debug-station-list"])

    assert received["station_list_debug"] is True


def test_diagnostic_classifies_required_endpoint_forbidden(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A required endpoint 403 is not reported as an authentication failure."""

    async def fake_run(
        _username: str,
        _password: str,
        _controller_id: int | None = None,
        _debug_program_response: bool = False,
        _debug_controller_detail: bool = False,
    ) -> None:
        raise RainBirdIQ4ForbiddenError(
            "/Satellite/private?access_token=secret#authorization=Bearer-secret"
        )

    monkeypatch.setattr(diagnostic, "_run_diagnostic", fake_run)
    monkeypatch.setattr("builtins.input", lambda _prompt: "test-user")
    monkeypatch.setattr(diagnostic.getpass, "getpass", lambda _prompt: "test-password")

    diagnostic.main([])

    output = capsys.readouterr().out
    assert output == (
        "Diagnostic error: IQ4 API endpoint forbidden: /Satellite/private (HTTP 403)\n"
    )
    assert "authentication failed" not in output
    assert "secret" not in output
    assert "test-password" not in output
