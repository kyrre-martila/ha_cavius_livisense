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

Hub diagnostics also include **Fire drill state** (planned, active, ended or cancelled, from live WebSocket events) and **Last fire drill** (cloud timestamp; may refer to when the drill was planned rather than when it started). Fire drill state is unknown until a live event arrives and is not restored after a restart. Drill lifecycle events never set smoke or heat flags; a concurrent drill never suppresses independent fire evidence.

Smoke and heat detectors also have:

- **Smoke alarm** (smoke detectors) or **Heat alarm** (heat detectors): active for a non-null detector alarm state (except an object carrying an explicit documented drill code), a documented fire REST event (1110/1111), or a fire WebSocket event. Hub-level events with no device ID flag all smoke and heat detectors on that hub. A device-specific event affects only that detector. Documented clear events clear the WebSocket flag; an absent REST event does not clear a WebSocket fire alarm. Unknown is shown until enough cloud data establishes a clear state.
- **Last tested** and **Last test result:** cloud-reported diagnostic fields, when provided.
- **Battery low:** detector event 1384 sets low; 3384 clears it. Unknown until reliable evidence is available. Battery percentage and the meaning of non-null warning fields are unverified.
- **Signal loss:** detector event 1381 sets loss; 3381 clears it. Missing or null signal strength does not mean a healthy connection.
- **Sensor trouble:** detector events 1380/1389 latch trouble. No recovery is documented; tests, battery recovery and guessed 3xxx codes never clear it. RF jamming (1344) has no verified detector scope and does not set detector trouble.

Smart Remotes expose only **Last cloud update**, when available. Cloud button press delivery and the meaning of remote battery/signal values are not confirmed, so no buttons, alarm or battery/signal entities are created.

Acknowledgement (9411) is not an alarm clear. Drill, acknowledgement, classification and other documented non-fire event codes do not establish fire during REST recovery. Unknown or missing REST event codes produce an unknown alarm state for their targets unless independent fire evidence is present. Recovered REST fire events remain active after acknowledgement removes them from the endpoint, until an actual clear event is received. Codes 9412/9413 classify an alarm and do not clear it. Existing hush/clear handling uses 1659/9421/9422. Reloading while an actual alarm remains active can still cause an unavailable-to-on transition and trigger Home Assistant automations; account for this in safety-related automation design.

Alarm and fault entities become unavailable when the hub reports offline or cloud synchronization fails. Fire flags and recovered fire records persist across ordinary WebSocket reconnects and REST refreshes, but are not stored across reloads or Home Assistant restarts. An acknowledged fire absent from both REST events and detector state cannot be recovered after reload. A missed clear can leave a recovered fire active until another clear or reload.

The integration listens for documented WebSocket state changes and polls the REST API every five minutes as a fallback. It reconnects after network failures and refreshes the REST snapshot after reconnecting. This is intended for diagnostics and is **not suitable for fire safety monitoring**.

## Reliable fault evidence

Battery events 1384/3384 and signal events 1381/3381 require an explicit detector
ID in `device.id` or `subject.id`, agreeing if both exist. Hub subjects, actor IDs,
names and missing/conflicting identities never broadcast detector faults. Ambiguous
evidence makes possible targets unknown. Hub battery codes 1302/3302 never affect
detector batteries. Trouble events 1380/1389 latch an observed failure; no recovery
is documented, and tests or guessed recovery codes never clear it.

Event timestamps and per-detector watermarks order received evidence. Older events
cannot overwrite newer ones; conflicting equal timestamps are unknown. Duplicates
do not renew confidence lost through a delivery gap. A delayed event generated
before connection is retained as an observation, rather than silently discarded.
It cannot establish current battery/signal state without proof that newer events
were not missed. A newer event generated within the current continuous WebSocket
stream can establish that diagnostic, including a delayed delivery within that
stream. Offline hub/reconnect boundaries invalidate old confidence. No arbitrary
delay grace period is used. Missing, invalid or future timestamps leave the
diagnostic unknown; untimed evidence cannot be ordered against later events.

The diagnostic entity attributes `last_observed_event_code`,
`last_observed_event_at` and `last_observed_event_source` expose an explicitly
attributed observation without raw payloads or device identifiers. **Last observed
means newest among received records, not latest in the cloud.**
`current_status_confirmed` distinguishes current live evidence from historical
observations. Trouble may be on as a latched failure report even when that attribute
is false. Smoke/heat alarm entities and behavior from PR #9 are unchanged.

Startup and periodic activity requests read one seven-day window per hub, at most
once every five minutes; reloads and reconnects share that budget. Only the first
300 returned records are processed. Pagination is undocumented: no page traversal,
older windows or immediate 429 retries are attempted. The documented
`meta.from`/`until`/`sort`/`hints` shape is accepted. Neither count/page fields nor
a short or empty list prove completeness. Activity failure does not stop ordinary
fire polling. Invalid ordering, duplicate records or wrong-hub references are
rejected. Even an attributed low/recovery event cannot establish current battery
or signal state if a newer opposite event might be omitted. History observations
are retained; they do not clear delivery gaps or establish healthy status.

Fault watermarks and observations survive ordinary integration reloads in an
entry-scoped memory cache. Reloads hide prior battery/signal values until new,
ordered live evidence establishes them. Full Home Assistant process restarts lose
that cache and require fresh evidence. Trouble remains a latched diagnostic report,
not proof that a detector is still faulty or has been repaired. Missing/null state
fields, normal signal strength and successful tests never establish battery health.

**Known HEATCAVIUS case:** the documented activity response can now expose an
observed 1384 on the battery entity, but cannot by itself restore a confirmed
current low-battery state. That still requires qualifying live evidence or an
additional captured/authoritative contract proving that newer events are not
omitted (or an authoritative current battery field). The app's battery source and
the laundry-room detector have not been verified. See [API notes](docs/livisense-api.md#fault-evidence-policy-pr-10)
and [verification](docs/pr-10-review.md).

## Known limitations

- Non-null `state.alarm` values and real-fire REST event shapes have not been captured. Only an object with a documented drill code (1119/3119/9811/9812, directly or under `event`) is treated as confirmed drill state. Other non-null values retain the active-alarm fallback, even during a drill; this ambiguity can cause false alarms. Unclassified REST records appear as unknown rather than being guessed to be fire.
- Real heat alarms have not been verified. Heat detectors reuse the documented fire alarm and clear events; no heat-specific event codes are assumed. Fire event payload shape is not verified against a real fire. Hub-level events without a detector ID are attributed to every detector on that hub. The integration is read-only.
- Battery percentage is not confirmed for smoke or heat detectors.
- The cloud API is unofficial and may change without notice.
- Internet and LiviSense cloud access are required. Cloud outages make current diagnostics unavailable.
- Detector connectivity is not separately verified by the documented REST data. A detector's last update is historical data, not proof of present communication.

> **Safety:** This integration is not a certified fire safety system. Never use it as a replacement for the detectors' built-in alarms or official Cavius safety functionality. Physical detectors must remain operational independently of Home Assistant.

## Licence

Licensed under the MIT License. See [LICENSE](LICENSE).

## Credits

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

