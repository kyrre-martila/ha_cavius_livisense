# PR #10: battery and detector fault evidence

Base: latest `main`, `182e563d30503c5cd29f6abd7943dd5d6538bd49`, including merged
PR #9. Reviewed coordinator, WebSocket lifecycle, entities, REST client, setup and
unload, existing tests, README, API notes and PR #9 before implementation.

## Cause and resulting behavior

The previous battery entity read a flag populated only by a live 1384/3384 event.
Startup fetched hub state and unacknowledged alarms, not activity. Thus null
battery/warning fields plus a healthy WebSocket could leave an old app-low
condition unknown indefinitely. The app's battery source is still unverified.

Fault evidence now has its own scoped timestamps and cache. Fire state logic,
fire attribution, drill diagnostics and alarm reconciliation from PR #9 remain
unchanged. Fault attribution rejects absent/conflicting detector identities;
actor IDs and hub battery events never establish a detector battery fault.
Missing fields never mean healthy. Trouble has no invented recovery.

The bounded activity path supports a complete, ordered seven-day suffix and
covered reload checkpoints. It refuses missing count metadata, partial pages,
contradictory pagination, duplicates, malformed/misordered timestamps and cross-hub
records. It preserves newer observed transitions over stale history. Disconnects
and offline hubs hide battery/signal values pending covered recovery or a fresh
live transition. Evidence is scoped to the config entry, retained in memory
through ordinary unload/reload, and never persisted to disk or logged.

## Source verification limits

Only the API documentation was available; no Cavius account credentials, raw live
fault capture or physical detector was used. The captured activity response has
no completeness/count/pagination guarantee. The defensive `total` and optional
`currentPage`/`lastPage` contract is tested using synthetic responses and is **not
verified service behavior**. The documented response without `total` deliberately
cannot restore state. No short-page, missing-event or null-field heuristic is used.

A fresh, scoped live 1384 can report the HEATCAVIUS battery low condition while
REST fields remain null. History can restore it only if the source supplies the
required complete evidence. This does **not** confirm the laundry-room report is
fixed. A sanitized capture or authoritative pagination/retention contract is still
needed to establish the app's source and make broader history recovery possible.
No fire drill, detector test, physical-device action or live cloud request was made.

## Automated verification

Python 3.12.14, Home Assistant 2025.1.4,
pytest-homeassistant-custom-component 0.13.205, Ruff 0.17.5.

Commands from the repository root:

```sh
/workspace/ha-venv/bin/python -m pytest -q --timeout=15 --disable-socket --allow-unix-socket
/workspace/ha-venv/bin/ruff check . --output-format concise
python -m compileall -q custom_components tests
git diff --check
```

- **189 passed in 4.20s**, no skips, with internet sockets disabled.
- Syntax compilation and whitespace checks passed.
- Ruff exits 1 with **33 existing findings**. A separate archive of the starting
  source has the same 33 file/rule/message findings; no new findings were added.
- The sandboxed HA test harness blocked during fixture teardown, as documented in
  PR #9. That run was terminated and all reported test results come from the
  completed run outside that sandbox, with internet sockets disabled. Existing
  HA harness cleanup workarounds were retained.

New coverage includes HEATCAVIUS startup recovery with null fields, scoped and
ambiguous attribution, unsupported devices, signal and trouble codes, stale and
equal timestamps, missing/invalid/future timestamps, old-stream replays, bounded
pagination and 429, incomplete/empty history, changed totals, duplicate records,
cross-hub records, expired and bridged checkpoints, REST/WS ordering, hub offline
and reconnect gaps, fresh live recovery, and an actual HA setup/unload/setup cycle.
Existing fire/drill tests still pass. HA 2026.11 and real cloud/device behavior
were not tested.
