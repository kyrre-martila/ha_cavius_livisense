"""Conservative, detector-scoped fault evidence, independent of fire alarms."""
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
    """Retain ordered evidence and hide it when a delivery gap is unresolved."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, str, str], tuple[datetime | None, bool | None]] = {}
        self.gaps: set[str] = set()
        self.fresh: set[tuple[str, str, str]] = set()
        self.coverage: dict[str, datetime] = {}
        self.last_attempt: datetime | None = None

    def mark_gap(self, hub_ids) -> None:
        self.gaps.update(hub_ids)
        self.fresh = {identity for identity in self.fresh if identity[0] not in self.gaps}

    def value(self, hub_id: str, device_id: str, key: str) -> bool | None:
        if (hub_id in self.gaps and key != "trouble"
                and (hub_id, device_id, key) not in self.fresh):
            return None
        return self.records.get((hub_id, device_id, key), (None, None))[1]

    def apply(self, hub_id, detectors, record, code, timestamp, *, live=False) -> None:
        if code not in FAULT_CODES:
            return
        key, value = FAULT_CODES[code]
        reference = detector_reference(record, hub_id)
        targets = {reference} if reference in detectors else set()
        if reference is None:
            # An unattributed recovery may invalidate any detector's old fault.
            targets = detectors
            value = None
        for device_id in targets:
            identity = (hub_id, device_id, key)
            previous = self.records.get(identity)
            if previous:
                previous_time, previous_value = previous
                if previous_time is None:
                    continue  # Later timestamps cannot order an untimed record.
                if timestamp is not None and timestamp < previous_time:
                    continue
                if timestamp == previous_time and value != previous_value:
                    value_for_device = None
                else:
                    value_for_device = value
            else:
                value_for_device = value
            self.records[identity] = (timestamp, value_for_device if timestamp else None)
            if live and timestamp is not None:
                self.fresh.add(identity)

    def reconcile(self, hub_id, detectors, events, start, end, code_of, time_of) -> None:
        """A complete suffix establishes latest transitions, never implicit health."""
        old = {identity: evidence for identity, evidence in self.records.items()
               if identity[0] == hub_id}
        self.records = {identity: evidence for identity, evidence in self.records.items()
                        if identity[0] != hub_id}
        # Only carry a checkpoint through a fully covered delivery gap.
        checkpoint = self.coverage.get(hub_id)
        if checkpoint is not None and start <= checkpoint <= end:
            self.records.update({identity: evidence for identity, evidence in old.items()
                                 if evidence[0] is not None and evidence[0] < start})
        for event in reversed(events):
            self.apply(hub_id, detectors, event, code_of(event), time_of(event))
        # Never let an older REST suffix replace newer observed evidence.
        # Untimed live evidence cannot be ordered even against this suffix.
        for identity, evidence in old.items():
            timestamp, value = evidence
            if timestamp is None:
                self.records[identity] = evidence
            elif timestamp >= start or identity[2] == "trouble":
                previous = self.records.get(identity)
                if previous is None or (previous[0] is not None and timestamp > previous[0]):
                    self.records[identity] = evidence
                elif previous[0] == timestamp and previous[1] != value:
                    self.records[identity] = (timestamp, None)
        self.coverage[hub_id] = end
        self.gaps.discard(hub_id)
