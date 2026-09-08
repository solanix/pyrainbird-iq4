# pyrainbird-iq4

Python library for communicating directly with Rain Bird IQ4 irrigation controllers.

## Goals

- Discover IQ4 controller capabilities
- Provide asynchronous Python access
- Support controllers, stations, programs, schedules, and runtime state
- Maintain a clean API suitable for a Home Assistant integration
- Provide comprehensive unit tests

## Hybrid read-only data model

IQ4 cloud endpoints remain the source for controller metadata, stations, programs,
schedules, and station runtimes. The local SIP protocol is the source for rapidly
changing controller state through `RainBirdIQ4LocalClient`:

- irrigation active/idle
- active station terminals
- available/configured station terminals
- rain sensor state
- remaining rain-delay days

The reverse-engineered `CurrentQueueRequest` may also expose the active program and
remaining runtime, but its response differs by controller family. The local diagnostic
probes this read-only command without exposing any irrigation control operation.

Local `/stick` requests use the controller-local password as their AES encryption key.
This is the controller password created during provisioning (the six-digit controller
PIN for Rain Bird 2.0 setups), not the IQ4 cloud-account password or Wi-Fi password.
Rain Bird stores it only in the controller; it cannot be derived from IQ4 cloud metadata.

Neither source currently provides a verified station enabled/disabled flag or last-run
timestamps. Local available terminals represent physical terminal capacity and must not
be presented as an invented station-enabled state.
