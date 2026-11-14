# PR #9 review and verification

Reviewed starting commit `5c53f7c0efe1635ef532e1779d8872b6279a8681` against
issue #5 and the captured structures in `livisense-api.md`.

## Findings addressed

- The original REST denylist admitted arbitrary event codes, including burglar,
  tamper, battery and malformed records, as fire. Recovery now recognizes
  1110/1111 as fire, ignores documented non-fire codes, and exposes unclassified
  records as unknown for their targets. Independent fire evidence takes priority.
- Filtering events alone left all non-null detector alarm states active. Explicit
  drill-code objects are now excluded; opaque states retain the active fallback.
  This object rule is defensive, not a claim about a captured non-null API shape.
- 9411 never clears fire. 9412/9413 classification and drill lifecycle events also
  cannot clear fire. Recovered REST fires survive disappearance from the
  unacknowledged endpoint, which can simply mean acknowledgement. Existing
  1659/9421/9422 clear/hush behavior is retained, including stale-record protection
  and detection of subsequent fires.
- Reload cancellation during WebSocket keepalive cleanup could be swallowed,
  allowing reconnection instead of stopping. The listener now preserves that
  cancellation. A forced-race regression fails with the original code.
- Diagnostic IDs remain unchanged and stable across HA registry reloads. Missing
  devices, offline hubs and failed synchronization make diagnostics unavailable.
  Missing/null values stay unknown; malformed, naive and UTC-overflow timestamps
  return no value. Valid timestamp values are timezone-aware UTC datetimes.
- README literal newline escapes and stale entity-count assertions were fixed.
  Cloud and device behavior is distinguished from synthetic fixture behavior.

## Verification

Environment: Python 3.12.14, Home Assistant 2025.1.4,
pytest-homeassistant-custom-component 0.13.205, pytest 8.3.4, Ruff 0.17.5.
Python 3.12 selected this compatible HA harness; HA 2026.11 was not tested.
No physical devices or cloud credentials were used.

Setup commands:

```sh
python -m venv /workspace/ha-venv
/workspace/ha-venv/bin/pip install pytest-homeassistant-custom-component ruff
```

Commands run from the repository root:

```sh
/workspace/ha-venv/bin/python -m pytest -q --timeout=30 --disable-socket --allow-unix-socket
/workspace/ha-venv/bin/ruff check . --output-format concise
python -m compileall -q custom_components tests
git diff --check
```

Results: **123 passed in 2.55s**, no skips. Syntax compilation and diff whitespace
checks passed. Ruff exits 1 with **33 existing findings**. Running the same Ruff
command against an archive of the starting commit also yields 33; comparing
file/rule/message multisets shows **zero new findings**. Unrelated lint cleanup
is outside issue #5. The findings concern import ordering, modernization, an
unused import, nested conditionals and broad exception handling.

Coverage includes synthetic flat/nested REST shapes, startup recovery, unknown
and non-fire codes, acknowledgement followed by REST disappearance, real-fire
routing and clearing, stale REST records, subsequent fires, WebSocket drill JSON
frames, duplicate cancellation events, concurrent drill/fire evidence, detector
state merges, failed refreshes, diagnostic values, HA entities and registry
reloads. Existing tests also cover authentication, discovery and diagnostics.

The initial sandboxed test run blocked in HA executor teardown; the suite was
then run outside that sandbox with internet sockets explicitly disabled. Existing
WebSocket test doubles were completed with async alarm-fetch responses, and the
reconnect test now synchronizes at the reconnect wait. Existing module-specific
HA cleanup fixture workarounds were retained; no new cleanup bypass was added.

For the reload cancellation regression, temporarily restoring the old cancellation
handler produced the expected failure (1 failed in 0.41s). The fixed handler was
restored afterward; the targeted test then passed (1 passed in 0.13s). The command is:

```sh
/workspace/ha-venv/bin/python -m pytest -q tests/test_websocket.py::test_reload_stop_during_keepalive_cleanup --timeout=10 --disable-socket --allow-unix-socket
```

## Remaining evidence gaps

Real-fire REST/WebSocket payloads and non-null detector alarm shapes have never
been captured in the available evidence. The reported drill REST recovery cause
was inferred by the reporter. Opaque drill-related state can therefore still
produce an active alarm; suppressing it based on a simultaneous drill would risk
hiding a real fire. Unknown REST codes produce unknown, not off or guessed fire.

Drill lifecycle is live diagnostic state, not persistent state or proof that all
concurrent alarms are drills. The cloud's last-drill timestamp may mean planned
time. Acknowledged fires absent from both REST state and events cannot be
reconstructed after reload/restart. Recovered fires can remain active after a
clear missed while offline. Hush/clear semantics retain the pre-existing mapping;
no physical-fire or hush behavior is claimed to have been validated.

PR #9 must remain open and unmerged pending maintainer review.
