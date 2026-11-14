# Cavius LiviSense for Home Assistant

Unofficial Home Assistant integration for Cavius devices connected through a LiviSense MHub. Supports smoke and heat detectors, with basic Smart Remote visibility.

> **Early development:** This integration exposes cloud reported alarm and fault states. It is not a certified safety monitor.

## Install with HACS

1. In HACS, open **Integrations** → menu → **Custom repositories**.
2. Add `https://github.com/kyrre-martila/ha_cavius_livisense` as an **Integration**.
3. Install **Cavius LiviSense** and restart Home Assistant.
4. Open **Settings** → **Devices & services** → **Add integration** and select **Cavius LiviSense**.

For manual installation, copy `custom_components/cavius_livisense` to your Home Assistant `custom_components` directory and restart.

## Configuration

Enter your Cavius Alarm account email and password in the Home Assistant UI. The integration validates them with the LiviSense login API. Each account can be configured once. If credentials expire or change, Home Assistant requests reauthentication.

The integration discovers all hub IDs returned by the account profile and supported devices reported by each hub: `SMOKECAVIUS` smoke detectors, `HEATCAVIUS` heat detectors and `REMOTECAVIUS` Smart Remotes. Each appears as a separate device linked to its hub. Unknown models are ignored.

## Current entities

- **Hub connectivity:** online only when the hub reports `state.status: "online"`. Other reported status values appear offline. The entity becomes unavailable if cloud synchronization fails.
- **WebSocket connectivity:** on while the cloud WebSocket is established; off during disconnect and reconnect. Unknown before its status is known. This does not establish physical hub or detector connectivity.
- **Last WebSocket message:** UTC time of the last valid incoming application message. Unknown until one arrives. A connected socket can have no recent application activity.
- **Last cloud update:** the documented `state.updatedAt` timestamp for each hub, detector and Smart Remote, when present. Detector and remote timestamps become unavailable when their parent hub is offline or cloud synchronization fails.

Hub diagnostics also include **Fire drill state** (planned, active, ended or cancelled, from live WebSocket events) and **Last fire drill** (cloud timestamp; may refer to when the drill was planned rather than when it started). Fire drill state is unknown until a live event arrives and is not restored after a restart. Drill events never set smoke or heat alarms.\n\nSmoke and heat detectors also have:

- **Smoke alarm** (smoke detectors) or **Heat alarm** (heat detectors): active for a non-null detector alarm state, a current unacknowledged alarm event, or a fire WebSocket event. Hub-level events with no device ID flag all smoke and heat detectors on that hub. A device-specific event affects only that detector. Documented clear events clear the WebSocket flag; an absent REST event does not clear a WebSocket fire alarm. Unknown is shown until enough cloud data establishes a clear state.
- **Last tested** and **Last test result:** cloud-reported diagnostic fields, when provided.\n- **Battery low:** event based warning, cleared by the documented recovery event. Battery percentage is unavailable.
- **Signal loss:** event based diagnostic, cleared by the documented recovery event.
- **Sensor trouble:** diagnostic set by trouble or self-test failure events. The API documents no recovery event, so this state persists until integration reload.

Smart Remotes expose only **Last cloud update**, when available. Cloud button press delivery and the meaning of remote battery/signal values are not confirmed, so no buttons, alarm or battery/signal entities are created.

Acknowledgement (9411) is not an alarm clear. Known drill, acknowledgement and classification events are excluded from REST unacknowledged-alarm recovery, preventing drills from being restored as smoke alarms. Unknown event codes remain conservatively eligible for alarm recovery. Reloading while an actual alarm remains active can still cause an unavailable-to-on transition and trigger Home Assistant automations; account for this in safety-related automation design.\n\nAlarm and fault entities become unavailable when the hub reports offline or cloud synchronization fails. Event flags persist across ordinary WebSocket reconnects and REST refreshes, but are not stored across Home Assistant restarts.

The integration listens for documented WebSocket state changes and polls the REST API every five minutes as a fallback. It reconnects after network failures and refreshes the REST snapshot after reconnecting. This is intended for diagnostics and is **not suitable for fire safety monitoring**.

## Known limitations

- Real heat alarms have not been verified. Heat detectors reuse the documented fire alarm and clear events; no heat-specific event codes are assumed. Fire event payload shape is not verified against a real fire. Hub-level events without a detector ID are attributed to every detector on that hub. The integration is read-only.
- Battery percentage is not confirmed for smoke or heat detectors.
- The cloud API is unofficial and may change without notice.
- Internet and LiviSense cloud access are required. Cloud outages make current diagnostics unavailable.
- Detector connectivity is not separately verified by the documented REST data. A detector's last update is historical data, not proof of present communication.

> **Safety:** This integration is not a certified fire safety system. Never use it as a replacement for the detectors' built-in alarms or official Cavius safety functionality. Physical detectors must remain operational independently of Home Assistant.

## Licence\n\nLicensed under the MIT License. See [LICENSE](LICENSE).\n\n## Credits

Special thanks to **André Rovik**, developer of the unofficial [Cavius Alarms app for Homey](https://homey.app/en-no/app/com.arovik.cavius/Cavius-Alarms-(unofficial)/test/), for sharing his reverse-engineering findings and technical documentation of the Cavius LiviSense cloud API. His research provided the foundation for this integration.

## Disclaimer

This is an independent, community-developed project. It is not affiliated with, endorsed by, or supported by Cavius or the LiviSense platform operators. All trademarks and product names belong to their respective owners.

## Download discovery diagnostics

Open **Settings → Devices & services → Cavius LiviSense**, open the integration
entry's three-dot menu and select **Download diagnostics**. Share the downloaded
file when troubleshooting missing devices. It includes counts and model/type
metadata for every cached device, including unsupported devices, plus state field
names without values. Downloading does not contact the cloud or refresh data.

Credentials, device/hub names and identifiers are excluded. Unrecognized or
potentially identifying strings are redacted; numeric model/type codes are also
redacted conservatively. `extras.appDeviceId` is included only when it exactly
matches the safe `type-model` pair. The supported-model flag recognizes smoke detectors, heat detectors and Smart Remotes.
