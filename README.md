# Cavius LiviSense for Home Assistant

Unofficial Home Assistant integration for Cavius smoke alarms connected through the Cavius Hub using the LiviSense cloud platform.

The goal of this project is to bring Cavius smoke alarms into Home Assistant, providing individual device monitoring, real-time status updates, and automation capabilities.

> **Project status: Early development**
>
> This integration is currently under development and is not yet ready for installation or production use.

## Features

The integration aims to support:

- **Smoke alarm monitoring** — Monitor individual Cavius smoke detectors in Home Assistant.
- **Real-time updates** — Receive device and hub events through the LiviSense WebSocket connection.
- **Battery warnings** — Detect low-battery events from individual smoke detectors.
- **Connectivity monitoring** — Monitor hub availability and detector communication issues.
- **Device discovery** — Automatically discover smoke detectors associated with your Cavius account.
- **Event monitoring** — Expose relevant events, including alarms, warnings, tests, and connectivity changes.
- **Home Assistant automations** — Use supported device states and events in automations and notifications.

Features will be implemented and validated incrementally.

## Supported hardware

This integration targets Cavius smoke alarms connected through the Cavius Hub using the LiviSense cloud platform.

The initial development focuses on:

- Cavius Hub (`MHub`)
- Cavius smoke detectors (`SMOKECAVIUS`)
- Cavius Alarm cloud accounts

Other devices and hub generations have not been verified.

## How it works

The integration communicates with the Cavius LiviSense cloud service.

It uses:

- **HTTPS REST API** for authentication, device discovery, and retrieving device states.
- **WebSocket** for real-time device and hub events.
- **Periodic synchronization** to recover from missed events or temporary connectivity interruptions.

No additional Homey hub, MQTT broker, or IFTTT connection is required.

An active internet connection and a working Cavius cloud account are required.

## Installation

Installation instructions will be provided when the first testable release is available.

The integration is intended to support installation through [HACS](https://www.hacs.xyz/).

## Configuration

The planned configuration process will use the Home Assistant user interface.

Users will sign in using their existing Cavius Alarm account credentials. Associated hubs and smoke detectors will then be discovered automatically.

## Safety and limitations

> **Important safety notice**
>
> This integration is not a certified fire safety system and must never replace the smoke detectors' built-in alarms or the official Cavius safety functionality.

Important limitations:

- The integration depends on an unofficial, undocumented cloud API that may change without notice.
- Cloud or internet outages may prevent Home Assistant from receiving updates.
- Real fire alarm detection has not yet been validated.
- Battery percentage is not currently available for supported smoke detectors.
- Device availability and alarm states must be handled conservatively to avoid incorrectly reporting a safe condition.

The physical smoke detectors must remain fully operational independently of Home Assistant.

## Development

This project is developed as a custom Home Assistant integration using Python and Home Assistant's asynchronous integration architecture.

Planned development areas include:

- Authentication and session management
- Automatic hub and device discovery
- Device state synchronization
- Real-time WebSocket event processing
- Smoke alarm and warning event handling
- Connectivity and availability monitoring
- Diagnostics and error handling
- HACS installation and distribution

Contributions, testing, and issue reports are welcome as development progresses.

## Credits

Special thanks to **André Rovik**, developer of the unofficial [Cavius Alarms app for Homey](https://homey.app/en-no/app/com.arovik.cavius/Cavius-Alarms-(unofficial)/test/), for sharing his reverse-engineering findings and technical documentation of the Cavius LiviSense cloud API.

His research provided the foundation for this integration.

## Disclaimer

This is an independent, community-developed project.

It is not affiliated with, endorsed by, or supported by Cavius or the LiviSense platform operators.

All trademarks and product names belong to their respective owners.
