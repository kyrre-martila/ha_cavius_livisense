# Cavius LiviSense for Home Assistant

Unofficial Home Assistant custom integration for Cavius smoke detectors connected through a LiviSense MHub.

> **Early development:** This integration exposes cloud reported alarm and fault states. It is not a certified safety monitor.

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
- **WebSocket connectivity:** on while the cloud WebSocket is established; off during disconnect and reconnect. Unknown before its status is known. This does not establish physical hub or detector connectivity.
- **Last WebSocket message:** UTC time of the last valid incoming application message. Unknown until one arrives. A connected socket can have no recent application activity.
- **Last cloud update:** the documented `state.updatedAt` timestamp for each hub and detector, when present. Detector timestamps become unavailable when their parent hub is offline or cloud synchronization fails.

Each SMOKECAVIUS detector also has:

- **Smoke alarm:** active for a non-null detector alarm state, a current unacknowledged alarm event, or a fire WebSocket event. Events with no identifiable detector flag all detectors on that hub. Documented clear events clear the WebSocket flag; a REST clear is accepted after a 90 second grace period. Unknown is shown until enough cloud data establishes a clear state.
- **Battery low:** event based warning, cleared by the documented recovery event. Battery percentage is unavailable.
- **Signal loss:** event based diagnostic, cleared by the documented recovery event.
- **Sensor trouble:** diagnostic set by trouble or self-test failure events. The API documents no recovery event, so this state persists until integration reload.

Alarm and fault entities become unavailable when the hub reports offline or cloud synchronization fails. Event flags persist across ordinary WebSocket reconnects and REST refreshes, but are not stored across Home Assistant restarts.

The integration listens for documented WebSocket state changes and polls the REST API every five minutes as a fallback. It reconnects after network failures and refreshes the REST snapshot after reconnecting. This is intended for diagnostics and is **not suitable for fire safety monitoring**.

## Known limitations

- Fire event payload shape is not verified against a real fire. Hub-level events without a detector ID are attributed to every detector on that hub. The integration is read-only.
- Battery percentage is not confirmed for `SMOKECAVIUS` detectors.
- The cloud API is unofficial and may change without notice.
- Internet and LiviSense cloud access are required. Cloud outages make current diagnostics unavailable.
- Detector connectivity is not separately verified by the documented REST data. A detector's last update is historical data, not proof of present communication.

> **Safety:** This integration is not a certified fire safety system. Never use it as a replacement for the detectors' built-in alarms or official Cavius safety functionality. Physical detectors must remain operational independently of Home Assistant.

## Credits

Special thanks to **André Rovik**, developer of the unofficial [Cavius Alarms app for Homey](https://homey.app/en-no/app/com.arovik.cavius/Cavius-Alarms-(unofficial)/test/), for sharing his reverse-engineering findings and technical documentation of the Cavius LiviSense cloud API. His research provided the foundation for this integration.

## Disclaimer

This is an independent, community-developed project. It is not affiliated with, endorsed by, or supported by Cavius or the LiviSense platform operators. All trademarks and product names belong to their respective owners.
