"""Scoped fault observations, distinct from evidence of current status."""
from __future__ import annotations

from datetime import datetime
from typing import Any

FAULT_CODES = {
    1384: ("battery", True), 3384: ("battery", False),
    1381: ("signal", True), 3381: ("signal", False),
    1380: ("trouble", True), 1389: ("trouble", True),
}


def detector_reference(record: dict[str, Any], hub_id: str) -> str | None:
    """Require agreeing explicit IDs; actor/name/serial are never identities."""
    references = []
    for key in ("device", "subject"):
        if key not in record:
            continue
        ref = record[key]
        if (not isinstance(ref, dict) or not isinstance(ref.get("id"), str)
                or not ref["id"] or ref.get("type") == "hub"
                or ref["id"] == hub_id):
            return None
        references.append(ref["id"])
    return references[0] if references and len(set(references)) == 1 else None


class FaultEvidence:
    """Order received evidence without claiming an incomplete list is current."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str, str], tuple[datetime | None, bool | None]] = {}
        self.confirmed: set[tuple[str, str, str]] = set()
        self.observations: dict[tuple[str, str, str], tuple[datetime, int | None, str]] = {}
        self.last_attempt: datetime | None = None

    def mark_gap(self, hub_ids) -> None:
        self.confirmed = {identity for identity in self.confirmed if identity[0] not in hub_ids}

    def value(self, hub_id: str, device_id: str, key: str) -> bool | None:
        identity = (hub_id, device_id, key)
        if key != "trouble" and identity not in self.confirmed:
            return None
        # Trouble is a latched report of failure, not proof of present repair.
        return self.records.get(identity, (None, None))[1]

    def attributes(self, hub_id: str, device_id: str, key: str) -> dict[str, Any]:
        """Expose facts about received events, never identities or raw payloads."""
        observation = self.observations.get((hub_id, device_id, key))
        if observation is None:
            return {}
        timestamp, code, source = observation
        return {"last_observed_event_at": timestamp.isoformat(),
                "last_observed_event_code": code,
                "last_observed_event_source": source,
                "current_status_confirmed": ((hub_id, device_id, key) in self.confirmed
                                             and self.value(hub_id, device_id, key) is not None)}

    def apply(self, hub_id, detectors, record, code, timestamp, *, current=False, source="activity") -> None:
        """A watermark orders observations; only fresh live delivery confirms status.

        A delayed event before the stream boundary is still evidence of an
        occurrence. Without a complete bridge it cannot prove the latest state.
        Duplicates never renew confirmation lost through a delivery gap.
        """
        if code not in FAULT_CODES:
            return
        key, value = FAULT_CODES[code]
        reference = detector_reference(record, hub_id)
        targets = {reference} if reference in detectors else set()
        if reference is None:
            targets = detectors
            value = None
        for device_id in targets:
            identity = (hub_id, device_id, key)
            observed = self.observations.get(identity)
            if reference is not None and timestamp is not None:
                if observed is None or timestamp > observed[0]:
                    self.observations[identity] = (timestamp, code, source)
                elif timestamp == observed[0] and observed[1] != code:
                    self.observations[identity] = (timestamp, None, "conflicting")
                elif timestamp == observed[0] and source == "websocket":
                    self.observations[identity] = (timestamp, code, source)
            previous = self.records.get(identity)
            if previous:
                previous_time = previous[0]
                if previous_time is None:
                    continue  # Cannot order later timestamps against an untimed event.
                if timestamp is not None and timestamp < previous_time:
                    continue
                if previous == (timestamp, value):
                    # First live delivery of an activity observation can confirm
                    # it if generated in this stream. A known live duplicate
                    # cannot renew confidence lost through a gap.
                    if (current and value is not None and timestamp is not None
                            and source == "websocket" and observed is not None
                            and observed[2] == "activity"):
                        self.confirmed.add(identity)
                    continue
                if timestamp == previous_time:
                    value_for_device = None  # Equal-time conflicts have no known order.
                else:
                    value_for_device = value
            else:
                value_for_device = value
            self.records[identity] = (timestamp, value_for_device if timestamp else None)
            self.confirmed.discard(identity)
            if current and timestamp is not None and value_for_device is not None:
                self.confirmed.add(identity)
