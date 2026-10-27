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
