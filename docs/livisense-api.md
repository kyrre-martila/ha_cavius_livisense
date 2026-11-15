# Cavius / LiviSense cloud API — reverse-engineering notes

Unofficial notes from building a Homey app for Cavius 5-year smoke alarms behind a Cavius
Hub ("MHub", LiviSense platform, iOS app "Cavius Alarm" 8.0). Shared so it can be reused,
e.g. for a Home Assistant integration. There is **no public API**; everything below comes
from intercepting the iOS app with mitmproxy (no certificate pinning) in October 2026.
It may change without notice.

Status legend: **confirmed** = seen in captured traffic and used in a working client;
**unverified** = inferred, not yet observed.

> Safety: this is a supplement to the detectors' own sirens, never a replacement. The
> detectors and hub alarm without the cloud. Don't trigger "test sensors" / fire drills
> against a live hub unless everyone in the house knows (it sounds all sirens).

## Overview

- The hub has **no local API** (no open TCP ports, no mDNS, no HTTP). It talks outbound
  to the cloud only. The old Nabto/UDP 5570 route applies to the older "Cavius Alarm"
  hub generation, not LiviSense.
- Cloud: `https://app.cavius.livi.cloud` — REST (JSON) + one WebSocket for realtime.
- Auth: `Authorization: Bearer <accessToken>` (RS256 JWT).

## Auth (confirmed)

```http
POST /auth/user/login
Content-Type: application/json

{"email": "...", "password": "..."}
```

Response:

```json
{
  "token": {
    "type": "bearer",
    "accessToken": "<jwt>",
    "refreshToken": "<opaque>",
    "expiresIn": 604800,
    "expiresAt": "2026-10-20T09:13:33.990+02:00"
  },
  "user": { "id": "<uuid>", "email": "...", "name": "...", "variant": "en", ... },
  "message": "logged in",
  "label": "user.login.successfully_logged_in"
}
```

- Access token lives **7 days**. A refresh endpoint was **not captured**; the Homey client
  simply logs in again ~1 h before `expiresAt`, and on any 401.
- `POST /auth/user/logout` exists. Logging out "everywhere" in the phone app sends a
  `user:loggedOut` frame on the WebSocket → log in again.
- `PUT /auth/user/devices` registers the phone for push (not needed for an integration;
  it rate-limits with 429).

## REST endpoints (confirmed)

| Method + path | Purpose |
|---|---|
| `GET /auth/user/profile` | user, `hubIds[]`, `hubs[]` with role, `features`, `permissions` |
| `GET /api/hubs?perPage=1000` | hubs on the account |
| `GET /api/hubs/{hubId}` | hub state, settings and **all detectors** — the main call |
| `GET /api/hubs/{hubId}/health` | `{status:"ok", label, issues[], hub{...}}` |
| `GET /api/hubs/{hubId}/events?perPage=300&filter={"category":"alarm","isAcknowledged":false}` | unacknowledged alarms (filter is URL-encoded JSON) |
| `GET /api/hubs/{hubId}/activity[?from=<unix>&until=<unix>]` | event history, 7-day windows, `sort: desc` |
| `GET /api/hubs/{hubId}/members?perPage=1000` | hub members |
| `GET /api/hubs/{hubId}/services` | linked services (`googlehome`, `ifttt`) |
| `GET /api/health` | backend health |
| `GET /content/en/labels.json` | i18n strings, incl. event code → text (`hub.event.<code>`) |

Errors come back as JSON with `message` and `label`.

### `GET /api/hubs/{hubId}`

```json
{
  "data": {
    "id": "<hub uuid>",
    "serialNo": "...",
    "name": "Home",
    "model": "MHub",
    "state": {
      "alarm": null,
      "status": "online",
      "mode": "disarm",
      "updatedAt": 1791873009,
      "lastAlarmAt": 1786207056,
      "lastEventAt": 1791860588
    },
    "settings": { "exitDelay": 30, "entryDelay": 30, "alarmDuration": 60 },
    "extras": { "enrolledAt": "...", "lastTestedAt": "...", "lastFireDrillAt": "..." },
    "devices": [
      {
        "id": "<device uuid>",
        "serialNo": "...",
        "name": "Kitchen",
        "type": "CaviusGroup",
        "model": "SMOKECAVIUS",
        "state": {
          "alarm": null,
          "warning": null,
          "batteryLevel": null,
          "signalStrength": 50,
          "updatedAt": 1791873009,
          "lastEventAt": 1791853574
        },
        "settings": { "isOnDuty": true, "isBurglarAlarm": true, "isSpecialSensor": false, "isActiveInHomeMode": false },
        "extras": { "appDeviceId": "CaviusGroup-SMOKECAVIUS", "lastTestResult": "passed" },
        "lastTestedAt": "2025-05-22T13:32:13.751+02:00"
      }
    ]
  }
}
```

Notes:
- `state.*At` values are **unix seconds**; top-level `createdAt`/`updatedAt` are ISO strings.
- `state.status`: `"online"` seen. Treat anything else as offline (and mark detectors
  unavailable rather than "no smoke").
- `state.mode`: `"disarm"` seen (the hub also does burglar-alarm modes; arm/disarm
  endpoint not captured).
- `batteryLevel` is **always null** for SMOKECAVIUS — low battery is only visible as
  events (see below) and possibly `warning`.
- `state.alarm` / `state.warning` were only ever `null`. Their non-null shape is
  **unverified**; the Homey app treats any non-null value as "alarm" / "warning".

### `GET /api/hubs/{hubId}/activity`

```json
{
  "data": [
    {
      "happenedAt": "2026-10-13T05:03:08.461+02:00",
      "label": "hub.event.9996",
      "type": "HubEvent",
      "code": "<event code, same as in label>",
      "actor":   { "id": "<hub uuid>", "name": "...", "type": "hub" },
      "subject": { "id": "<hub uuid>", "name": "...", "type": "hub" }
    }
  ],
  "meta": { "from": 1791270822, "until": 1791875622, "sort": "desc", "hints": {} }
}
```

## WebSocket (confirmed)

```
GET wss://app.cavius.livi.cloud/ws?token=<accessToken>
```

- Token goes in the query string (URL-encode it).
- Keep-alive: the client sends `{"ping":1}` every 30 s.
- Frames are JSON: `{"name": "<frame name>", "data": {...}}`.
- On (re)connect, re-fetch the hub + unacknowledged alarms to catch up.

| Frame `name` | `data` | Meaning |
|---|---|---|
| `hub:state` | `{hub:{id, state, ...}}` | hub state changed |
| `hub:updated` | `{hub:{...}}` | hub metadata changed |
| `device:state` | `{hub:{id}, device:{id,...}, state:{...}}` | detector state changed (merge `state`) |
| `device:updated` | `{hub:{id}, device:{...}}` | detector metadata changed |
| `hub:event` | see below | an event (test, battery, fire, online/offline...) |
| `user:loggedOut` | — | session revoked → log in again |

### `hub:event` (confirmed for a manual test)

Captured when pressing "test" in the app (code 1604):

```json
{
  "name": "hub:event",
  "data": {
    "hub": { "id": "<hub uuid>" },
    "event": { "type": "hub.event", "severity": 2, "code": 1604, "label": "hub.event.1604" },
    "category": "event",
    "subject": { "id": "<hub uuid>", "type": "hub", "name": "..." },
    "actor":   { "type": "member", "name": "..." },
    "happenedAt": "..."
  }
}
```

A manual test does **not** set `state.alarm` and creates no unacknowledged alarm event.

**Unverified:** a real fire. Assumption used in the Homey app: same frame with
`code` 1110/1111, `category: "alarm"`, and the detector as `subject` (`type` ≠ `"hub"`)
or in `data.device`. If the detector can't be identified, flag *all* detectors.
If you capture a real alarm or a fire drill, please share the frame.

## Event codes

From `labels.json` (`hub.event.<code>`) plus a few seen in activity. The codes look like
SIA/Contact-ID style: 1xxx = new event, 3xxx = restore.

| Code | Meaning | Used as |
|---|---|---|
| 1110 | Fire alarm triggered by {{subject.name}} | **fire** |
| 1111 | Smoke alarm | **fire** |
| 1119 / 3119 | Fire drill started / ended | drill |
| 9811 / 9812 | Fire drill planned / cancelled | drill |
| 1659 | Smoke/heat cleared, hush button pushed | clear (that detector) |
| 9421 | Alarms hushed | clear (all) |
| 9422 | Alarm cleared | clear (all) |
| 1384 / 3384 | Detector battery low / recovered | battery |
| 1381 / 3381 | Detector loss of signal / recovered | signal |
| 1302 / 3302 | Hub (base) battery low / recovered | hub |
| 1301 / 3301 | AC power loss / recovered | hub |
| 1389 | Device self-test failure | trouble |
| 1380 | Sensor trouble | trouble |
| 1344 | RF jamming | trouble |
| 1137 / 3137 | Device tamper alarm / recovered | — |
| 1130 / 3130 | Burglar alarm / recovered | — |
| 1400 / 3408 | Alarm disarmed / armed | mode |
| 1602 | Periodic test | info |
| 1604 | Manual test on sensors | info |
| 9995 / 9996 | Hub disconnected / connected | online state |

Fetch `labels.json` for the full, current list (it also has 1137, 1138 entry delay, 1168/1169
high/low limit, 3154 water leak recovered, 3442 exit delay ended, ...).

## Suggested client design (what works in the Homey app)

1. Log in with email + password; store the token and its `expiresAt`; re-login on 401
   or near expiry.
2. `GET /auth/user/profile` → `hubIds`; `GET /api/hubs/{id}` → entities per detector
   (`model == "SMOKECAVIUS"`), keyed by device `id` (stable UUID).
3. Open the WebSocket; reconnect with backoff (5 s doubling to 5 min).
4. Poll as a safety net: unacknowledged alarms every 30 s, full hub every 60 s
   (the realtime frame for a real fire is not verified yet).
5. Smoke = `state.alarm` non-null **or** an unacknowledged alarm event whose
   `subject.id`/`device.id` is the detector **or** a recent fire `hub:event`.
   Clear on 1659/9421/9422, or when the poll shows no unacknowledged alarms (after a
   ~90 s grace period).
6. Battery/signal problem flags from 1384/1381, cleared by 3384/3381.
7. Hub offline → mark detectors unavailable.

Reference implementation (Node, no framework deps): `lib/LiviClient.js`,
`lib/Events.js`, `lib/DeviceState.js` in the Homey app.

## Open questions

- Token refresh endpoint (a `refreshToken` is issued, but its use wasn't captured).
- Real fire alarm: REST `events?category=alarm` item shape, `state.alarm` value,
  WebSocket frame.
- Where low battery shows besides events 1384/3384.
- Arm/disarm and "test sensors" endpoints.
- IFTTT ("Works With Cavius LiviSense") and Google Home are linked services on the same
  backend, so the cloud is the intended integration point.


## Issue #5 evidence and integration policy

The [issue #5 report](https://github.com/kyrre-martila/ha_cavius_livisense/issues/5)
captured drill WebSocket codes 9811, 1119, 9812 and acknowledgement 9411. It did
not capture the REST list while the drill was unacknowledged or a non-null
`state.alarm`. The real-fire REST record and WebSocket shapes remain unverified.
Nested `event.code` (WebSocket) and direct `code` (activity) are supported by the
integration; REST tests of these shapes are synthetic, not captured fire fixtures.

Unlike the historical suggested client design above, REST recovery establishes
fire only from label-confirmed 1110/1111. Documented drill, classification,
acknowledgement, test, fault and non-fire alarm codes cannot establish fire.
Unknown/missing codes produce an unknown alarm state for the relevant detectors
unless independent fire evidence exists. The original records remain cached.
Acknowledgement/classification never clears a recovered fire; disappearance from
the unacknowledged endpoint also cannot clear it. Existing clear/hush events
1659/9421/9422 retain their existing handling.

A detector alarm object explicitly carrying a drill code, directly or in
`event.code`, is treated as confirmed drill state. This is a defensive rule, not
a claim that this non-null shape has been captured. Opaque values, including
free text mentioning a drill, retain the active-alarm fallback. A simultaneous
hub drill does not prove a detector alarm is a drill and never suppresses fire.

Recovery caches are in memory. After reload/restart, a fire already acknowledged
and absent from both REST events and detector state cannot be recovered. A clear
missed while offline can leave a recovered fire active until reload or another
clear; a real new fire always overrides a prior clear. These are cloud-data
limitations, not verified physical-fire behavior.


## Fault evidence policy (PR #10)

The reproduced HEATCAVIUS app-low/HA-unknown case is consistent with the old
implementation: only live 1384/3384 and 1381/3381 updated diagnostic flags;
startup read hub state and unacknowledged **alarm** events, not activity. Neither
null `batteryLevel`/`warning`/`signalStrength` nor online connectivity supplies a
baseline. The app's additional battery source remains unknown. No account or
physical detector was accessed for this change.

Fault attribution is deliberately stricter than fire attribution. Only explicit,
agreeing `subject.id`/`device.id` belonging to a supported detector on that hub
are accepted. Hub subjects and conflicting/malformed references are ambiguous;
`actor`, names, serials and single-detector guessing are never used. Ambiguous
faults/recoveries invalidate possible targets rather than set a common state.
An explicit unsupported/unknown device affects no known detector. Fire routing
and all smoke/heat recovery rules from PR #9 are unchanged.

1384/3384 and 1381/3381 are paired detector transitions. 1302/3302 are hub-only.
1380/1389 establish detector trouble, but have no documented clear. Neither a
successful test nor guessed 3380/3389 clears it. 1344 is RF jamming; the table's
historical "trouble" grouping is not evidence of detector scope or recovery,
so it does not set individual detector trouble. No drill or test is initiated.
No unverified non-null current field is interpreted as a specific fault or health.

### Documented activity observations and uncertainty (PR #10b)

Repository review found only the example above: `data`, with
`meta.from`, `meta.until`, `meta.sort: "desc"` and opaque `meta.hints`. No captured
fixture demonstrates `total`, `currentPage`, `lastPage`, a next-page token,
retention completeness or an unfiltered newest-page guarantee. The fixture
`tests/fixtures/activity_documented.json` copies that documentation example with
its original placeholders; it is not a new authenticated capture. Detector fault
variants used in tests are explicitly synthetic.

The previous PR #10 implementation required invented count/page metadata. That
requirement and the corresponding speculative completeness path have been removed.
The client now accepts the documented envelope and requests only `from` and `until`
for one seven-day window, once per five minutes per hub, sharing an attempt budget
through ordinary reloads and reconnects. It processes at most the first 300 returned
records. This is a processing limit, not a proven server-side page/body-size limit.
No hints or next-page URLs are guessed or followed, no older windows are scanned,
and 429/network errors are not immediately retried. Time bounds must match the
request; timestamps must be valid, within bounds, descending, without duplicates
or cross-hub references. A malformed list supplies no trusted recovery evidence.

Sort order only orders returned records. A short page, empty hints, an explicit
count, or an attributed 1384/3384 does **not** prove that a newer opposite event
was not omitted. Thus battery/signal history supplies occurrence observations,
not current truth and not a complete bridge through reload/reconnect gaps.
1380/1389 can latch a historical failure report because no clear is documented;
this still does not assert that a physical problem remains unrepaired.

Each fault entity exposes only the code/time/source of the newest **observed**
scoped record, and `current_status_confirmed`. This wording does not claim latest
in the cloud. No raw payload, serial, credential, name or device ID is exposed by
these attributes. A newer unconfirmed history record invalidates an older live
value rather than silently preserving stale health; older history cannot overwrite
a newer live watermark. Missing fields never imply a healthy detector.

### Delayed live events, replay and delivery gaps

`happenedAt` is compared with both the per-detector/diagnostic watermark and the
current WebSocket/hub connection boundary. Preconnection events are no longer
dropped. They are retained as observed occurrences, but cannot establish current
battery/signal status unless freshness is independently established. There is no
arbitrary grace period: delay and historical replay are indistinguishable from
timestamps alone when newer events may have been missed.

A timestamped transition generated within the current continuous live stream,
not in the future or older than received evidence, can establish a current value.
A delayed delivery within that stream remains valid. Normal repeated online
status frames do not reset its boundary. Older records are ignored for state;
exact duplicates cannot renew confidence after a gap. Equal-time contradictory
transitions are unknown. Missing/invalid/future event times leave an unorderable
record and keep the affected status unknown in the retained cache. An ambiguous
identity cannot set a specific detector, even if the timestamp is fresh.

On disconnect, offline hub or ordinary unload, prior battery/signal confirmation
is invalidated. A reconnect/online transition creates a new boundary. A transition
from inside the gap is retained but unconfirmed; a new live event after the new
boundary can establish that diagnostic. A replay of an old recovery cannot
silently restore a healthy value. Partial activity cannot close the gap.

This relies on normal delivery of the documented realtime stream; the available
API has no documented sequence numbers/replay cursor that could prove delivery
completeness. No live fault payload or app battery source was verified in this
work. Fault traffic is not promoted to fire; PR #9 smoke/heat behavior is unchanged.

Only fault watermarks/observations and the attempt budget are retained in the
entry-scoped memory cache across ordinary reloads. The cache has no credentials,
serials or raw payloads and is not written to disk or logs. A full HA process
restart loses it. Trouble is a latched failure report with no invented clear;
battery/signal require fresh current evidence after a gap.

**Critical outcome:** the documented response can surface a historical low-battery
observation on the HEATCAVIUS battery entity, but cannot prove the laundry-room
detector's current low state. Current status remains conditional on qualifying
live events or additional captured API evidence establishing newest-event
coverage/current-field semantics. No authenticated live access, physical-device
action, fire drill, detector test or battery replacement was performed.
See [PR #10 verification](pr-10-review.md).
