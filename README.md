# Cavius LiviSense for Home Assistant

Unofficial Home Assistant custom integration for Cavius smoke detectors connected through a LiviSense MHub.

> **Early development:** This integration provides cloud discovery and limited diagnostics. It does not detect or clear fire alarms.

## Install with HACS

1. In HACS, open **Integrations** → menu → **Custom repositories**.
2. Add `https://github.com/kyrre-martila/ha_cavius_livisense` as an **Integration**.
3. Install **Cavius LiviSense** and restart Home Assistant.
4. Open **Settings** → **Devices & services** → **Add integration** and select **Cavius LiviSense**.

For manual installation, copy `custom_components/cavius_livisense` to your Home Assistant `custom_components` directory and restart.

## Configuration

Enter your Cavius Alarm account email and password in the Home Assistant UI. The integration validates them with the LiviSense login API. Each account can be configured once. If credentials expire or change, Home Assistant requests reauthentication.

The integration discovers all hub IDs returned by the account profile and the `SMOKECAVIUS` detectors reported by each hub. Hubs and detectors appear as separate devices.

## Current entities

- **Hub connectivity:** online only when the hub reports `state.status: "online"`. Other reported status values appear offline. The entity becomes unavailable if cloud synchronization fails.
- **Last cloud update:** the documented `state.updatedAt` timestamp for each hub and detector, when present. Detector timestamps become unavailable when their parent hub is offline or cloud synchronization fails.

The integration polls the documented REST API every five minutes. This interval is intended for diagnostics and is **not suitable for fire safety monitoring**.

## Known limitations

- No WebSocket, fire alarm, warning, battery, or device control entities are implemented.
- Battery percentage is not confirmed for `SMOKECAVIUS` detectors.
- The cloud API is unofficial and may change without notice.
- Internet and LiviSense cloud access are required. Cloud outages make current diagnostics unavailable.
- Detector connectivity is not separately verified by the documented REST data. A detector's last update is historical data, not proof of present communication.

> **Safety:** This integration is not a certified fire safety system. Never use it as a replacement for the detectors' built-in alarms or official Cavius safety functionality. Physical detectors must remain operational independently of Home Assistant.

## Credits

Special thanks to **André Rovik**, developer of the unofficial [Cavius Alarms app for Homey](https://homey.app/en-no/app/com.arovik.cavius/Cavius-Alarms-(unofficial)/test/), for sharing his reverse-engineering findings and technical documentation of the Cavius LiviSense cloud API. His research provided the foundation for this integration.

## Disclaimer

This is an independent, community-developed project. It is not affiliated with, endorsed by, or supported by Cavius or the LiviSense platform operators. All trademarks and product names belong to their respective owners.
