# PR #10 review and verification — follow-up 10b

Continues the existing PR #10, branch `fix/reliable-detector-fault-status`.
Reviewed head `ec8e420f0783bb4100b422a7a7803b407ca840d7`, based on main
`182e563d30503c5cd29f6abd7943dd5d6538bd49` including merged PR #9.
This report supersedes the original PR #10 completeness and recovery claims.

## Review findings and corrections

The original `timestamp < _fault_stream_started: return` conflated delayed delivery
with historical replay. A real event can be generated just before connection and
arrive just afterward. Those events are now retained, ordered and exposed as
observations. They do not become current merely because they were delivered.

Per-hub/device/diagnostic timestamps reject state changes older than the existing
watermark. Exact live duplicates do not reconfirm state after a gap. First live
delivery of a record previously seen only in activity can confirm it if its
timestamp belongs to the current stream; subsequent live duplicates cannot.
Equal-time conflicts
remain unknown. A newer event generated within the current live stream can set or
clear the current diagnostic, including delayed delivery within that stream. No
arbitrary grace period was added. A preconnection/gap event whose freshness cannot
be proved remains an occurrence observation and an unknown current value. Newer
unconfirmed evidence invalidates older confirmation rather than retaining stale
health. Offline hubs and closed sockets cannot confirm current state; ordinary
repeated online frames do not create a new connection boundary.

The original API path required `total`, `currentPage`/`lastPage` and then treated
those fields as a completeness contract. No repository capture supports that
contract. The only documented envelope has `from`, `until`, `sort`, opaque `hints`.
The speculative traversal/completeness path and checkpoint-bridging inference have
been removed. The documented response is now accepted without inventing metadata.

The client requests only one seven-day window per hub, once per five minutes,
sharing the budget across reloads/reconnects. It processes at most 300 returned
records and never follows unknown hints/pages or scans earlier windows. This does
not prove a server body-size limit. Errors/429 have no immediate retry. Valid
observations survive; missing/empty/partial history never establishes health.
An explicit low or recovered event proves an occurrence, not its continued truth:
a newer opposite event could be omitted even from a descending list.

Fault entities expose only `last_observed_event_code`, `last_observed_event_at`,
`last_observed_event_source`, and `current_status_confirmed`. "Last observed" means
newest among received scoped records, not latest in the cloud. Attributes contain
no identifiers, serials, names, credentials or raw payloads. Untimed records cannot
be ordered against later evidence and keep the affected state unknown in the cache.

## Transitions and persistence reviewed

- 1384/3384 and 1381/3381 set/clear only their explicitly attributed detector when
  current live evidence and ordering permit. Hub codes 1302/3302 never affect them.
- Missing/conflicting identities never guess a device or broadcast a fault.
  Attributed unsupported devices do not affect supported detectors.
- 1380/1389 latch observed trouble, including history observations. No clear is
  invented for successful tests or guessed 3380/3389. A latched failure report
  does not claim that a physical repair has or has not occurred. 1344 has no
  verified detector scope and does not become detector trouble.
- Ordinary reloads retain fault observations, watermarks and the attempt budget
  in an entry-scoped memory cache. They invalidate battery/signal confirmation;
  a replay or partial history cannot silently restore healthy status.
- Full HA process restarts lose the cache. New history observations can be shown,
  but battery/signal need fresh qualifying live evidence to become current.
- Smoke/heat alarm logic, fire routing and drill/test behavior from PR #9 are
  unchanged. All original fire/drill regressions remain in the suite.

## Actual evidence and critical self-review

Repository searches found no authenticated fault/activity captures beyond the API
notes. `tests/fixtures/activity_documented.json` copies the original documentation
example with its placeholders. It is not a newly captured fault response. Detector
fault substitutions/envelopes in tests are synthetic and identified as such.
No authenticated Cavius access was available. No live Cavius request, physical
alarm, fire drill, detector test or battery replacement was performed.

The documented response now contributes useful historical observations instead
of being discarded. It still cannot prove a current battery-low or recovered
state. Sort order alone does not prove an unfiltered newest prefix or the absence
of omitted newer events; count fields would not prove those semantics either.
This is why positive low evidence and positive recovery evidence are both kept
separate from current state. Successful synthetic tests do not fill that gap.

**Known HEATCAVIUS outcome:** an observed 1384 can be visible in the battery entity
attributes, but the documented activity response alone cannot restore a confirmed
current low-battery state. Current status remains conditional on a qualifying
ordered live event. To recover the existing laundry-room condition after restart
without a new event requires additional captured/authoritative API evidence:
either current battery-field semantics or guaranteed newest-event coverage,
filtering, retention and pagination semantics. The app's battery source remains
unknown. This change does not claim that the reported detector is fixed.

The live confirmation path relies on normal realtime delivery; the API notes
provide no sequence number/replay cursor with which to prove delivery completeness.
Records predating a connection gap remain unconfirmed. Ambiguous timestamps or
identities intentionally reduce confidence. This trades availability for avoiding
unsupported healthy/faulty claims. Historical trouble is a latched event report,
not an asserted current physical condition. No relaxed assumption is hidden in
an undocumented metadata check or delay threshold.

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

Final results: **205 passed in 5.01s**, no skips. Compilation and `git diff --check`
passed. Ruff exits 1 with **33 pre-existing findings**; a separate archive of the
starting PR #10 source has the identical file/rule/message multiset: **zero new
findings and zero removed findings**. HA fixture teardown requires execution
outside the filesystem/network sandbox, as already recorded for PR #9; test
internet sockets remain explicitly disabled. Existing cleanup workarounds are
retained. HA 2026.11 and physical/cloud behavior are untested.

The revised suite retains original fire/drill coverage, adjusts tests of the
removed speculative completeness contract to the documented observation contract,
and adds focused delayed-delivery, continuous-stream ordering, preconnection/gap,
duplicate replay, partial-history/omitted-newer-event, documented-envelope,
rate-budget and real HA reload regressions. An older assertion that a report
received while a hub is offline could confirm a current battery fault was changed
to require unknown while retaining the observed event.
