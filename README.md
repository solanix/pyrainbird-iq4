# pyrainbird-iq4

Asynchronous Python library for Rain Bird IQ4 irrigation controllers. It reads controller,
station and program configuration from the IQ4 cloud, and reads live state from — and
starts or stops watering on — the controller over its local network interface.

It is the protocol layer behind the `rainbird_iq4` Home Assistant custom integration, and
can be used on its own.

> **Not affiliated with Rain Bird.** The IQ4 cloud endpoints and the local protocol
> behaviour described here are observed, not vendor-documented, and may change.
> Start and stop commands physically control irrigation.

## Installation

```bash
pip install pyrainbird-iq4
```

Requires Python 3.13 or newer.

## Cloud: controllers, stations and programs

```python
import aiohttp
from pyrainbird_iq4.cloud.auth import authenticate
from pyrainbird_iq4.cloud.client import RainBirdIQ4CloudClient


async def main() -> None:
    token = await authenticate("you@example.com", "iq4-password")
    async with aiohttp.ClientSession() as session:
        client = RainBirdIQ4CloudClient(token, session=session)
        for controller in await client.get_controllers():
            for station in await client.get_stations(controller.id):
                print(controller.name, station.terminal, station.name, station.is_enabled)
```

Other calls include `get_programs`, `get_scheduled_start_times`, `get_station_runtimes`
and `get_connection_statuses`. Sign-in can be blocked by the IQ4 web application
firewall; this raises `RainBirdIQ4WAFChallengeError`, while rejected credentials raise
`RainBirdIQ4CredentialsError`.

`Station.is_enabled` is `True`, `False` or `None` (unknown). It is derived from IQ4's
`isLocked` flag, which — counterintuitively — is `true` for **enabled** stations.

## Local: live state and control

```python
import aiohttp
from pyrainbird_iq4 import RainBirdIQ4LocalClient


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        client = await RainBirdIQ4LocalClient.connect(session, "192.0.2.10", "123456")
        state = await client.get_realtime_state()  # active terminals and current queue
        print(state.state, state.active_terminals, state.remaining_runtime_seconds)
        # Irrigation enabled, terminal capacity, rain sensor and rain delay.
        slow = await client.get_slow_state()
        print(slow.rain_sensor_active, slow.rain_delay_days)
```

The password is the **controller-local** password set during provisioning (the six-digit
PIN on Rain Bird 2.0 setups), not the IQ4 cloud password. The controller uses a
self-signed certificate; each request uses a fresh HTTPS connection.

`available_terminals` is the controller's physical terminal capacity, not a list of
enabled stations — use the cloud `Station.is_enabled` for that.

### Starting and stopping

```python
await client.start_station(terminal=1, duration_minutes=5)  # 1-30 minutes
started = await client.wait_for_station_active(1)  # up to 5 read-only checks

await client.stop_irrigation()
stopped = await client.wait_for_idle()
```

Each call sends exactly one command and never retries. A successful return means the
controller **acknowledged** the command, not that water is flowing; the `wait_for_*`
helpers confirm it with read-only polls. If a command raises a timeout or connection
error, the controller may or may not have acted — check its state before sending
anything again. The library validates argument types and ranges only; deciding whether
a station may be started (enabled, idle, rain) is up to the caller.

## Command-line diagnostics

```bash
rainbird-iq4-diagnostic                    # cloud: sites, controllers, stations, programs
rainbird-iq4-local-diagnostic 192.0.2.10   # local: read-only live state
rainbird-iq4-local-diagnostic 192.0.2.10 --check-control-support
```

Both prompt for credentials and print no secrets. The local diagnostic's write test
requires an explicit confirmation flag and physically waters a station; see `--help`.

## Compatibility

Tested with an IQ4-connected controller on firmware 3.11.0. Other controllers and
firmware versions may respond differently, particularly to the reverse-engineered
`CurrentQueueRequest` used for queue and remaining-runtime data.

## Development

```bash
pip install -e ".[dev]"
pytest && ruff check . && ruff format --check . && mypy src
```

## License

MIT
