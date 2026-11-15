"""First live delivery uses the immutable prior observation, not its replacement."""
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.cavius_livisense.faults import FaultEvidence

AT = datetime(2026, 11, 15, tzinfo=UTC)
DEVICE = {"subject": {"id": "heat"}}
TRANSITIONS = [(1384, "battery", True), (3384, "battery", False),
               (1381, "signal", True), (3381, "signal", False)]


@pytest.mark.parametrize("code,key,expected", TRANSITIONS)
def test_replacing_observation_preserves_original_tuple_for_first_live_confirmation(code, key, expected):
    evidence = FaultEvidence()
    identity = ("hub", "heat", key)
    evidence.apply("hub", {"heat"}, DEVICE, code, AT)
    original = evidence.observations[identity]
    assert evidence.value(*identity) is None

    evidence.apply("hub", {"heat"}, DEVICE, code, AT, current=True, source="websocket")
    # This is the exact object-semantics premise disputed in review: assigning
    # a new tuple to the dict leaves the old tuple (and apply's local) intact.
    assert evidence.observations[identity] is not original
    assert original == (AT, code, "activity")
    assert evidence.observations[identity] == (AT, code, "websocket")
    assert identity in evidence.confirmed
    assert evidence.value(*identity) is expected

    # A second live delivery in the same stream preserves confirmation.
    evidence.apply("hub", {"heat"}, DEVICE, code, AT, current=True, source="websocket")
    assert evidence.value(*identity) is expected
    evidence.mark_gap({"hub"})
    assert evidence.value(*identity) is None
    # Even if a caller labels the timestamp current, an already seen live
    # duplicate cannot restore confirmation lost across a gap.
    evidence.apply("hub", {"heat"}, DEVICE, code, AT, current=True, source="websocket")
    assert identity not in evidence.confirmed
    assert evidence.value(*identity) is None


@pytest.mark.parametrize("code,opposite,key", [(1384, 3384, "battery"), (3384, 1384, "battery"),
                                              (1381, 3381, "signal"), (3381, 1381, "signal")])
def test_equal_time_conflict_cannot_be_reconfirmed_by_replaying_activity_or_live(code, opposite, key):
    evidence = FaultEvidence()
    identity = ("hub", "heat", key)
    evidence.apply("hub", {"heat"}, DEVICE, code, AT)
    evidence.apply("hub", {"heat"}, DEVICE, opposite, AT, current=True, source="websocket")
    assert evidence.records[identity] == (AT, None)
    for source, replay in (("activity", code), ("websocket", code), ("websocket", opposite)):
        evidence.apply("hub", {"heat"}, DEVICE, replay, AT, current=True, source=source)
        assert evidence.value(*identity) is None
        assert identity not in evidence.confirmed
    # Older observations, whatever their source, cannot displace the conflict.
    evidence.apply("hub", {"heat"}, DEVICE, code, AT - timedelta(seconds=1), current=True, source="websocket")
    assert evidence.records[identity] == (AT, None)
    # A distinctly newer, qualifying live transition may resolve uncertainty.
    evidence.apply("hub", {"heat"}, DEVICE, opposite, AT + timedelta(seconds=1), current=True, source="websocket")
    assert evidence.value(*identity) is (opposite in (1384, 1381))


@pytest.mark.parametrize("code,key,expected", TRANSITIONS)
def test_older_first_live_delivery_of_history_cannot_replace_newer_live_evidence(code, key, expected):
    evidence = FaultEvidence()
    identity = ("hub", "heat", key)
    opposite = {1384: 3384, 3384: 1384, 1381: 3381, 3381: 1381}[code]
    evidence.apply("hub", {"heat"}, DEVICE, code, AT)
    evidence.apply("hub", {"heat"}, DEVICE, opposite, AT + timedelta(seconds=1), current=True, source="websocket")
    newer = evidence.observations[identity]
    evidence.apply("hub", {"heat"}, DEVICE, code, AT, current=True, source="websocket")
    assert evidence.observations[identity] == newer
    assert evidence.value(*identity) is (not expected)
