"""Regression tests for issue #5: drills must not become fire alarms."""
import pytest

pytest.importorskip("homeassistant")

from custom_components.cavius_livisense.coordinator import LiviSenseCoordinator


@pytest.fixture
def coordinator():
    instance = object.__new__(LiviSenseCoordinator)
    instance.data = {
        "hub": {"hub": {
            "id": "hub",
            "devices": [
                {"id": "smoke", "model": "SMOKECAVIUS", "state": {"alarm": None}},
                {"id": "heat", "model": "HEATCAVIUS", "state": {"alarm": None}},
            ],
        }}
    }
    instance.flags = {}
    instance.alarm_events = {}
    instance._cleared_alarms = {}
    instance._cleared_device_states = {}
    return instance


@pytest.mark.parametrize("code", [1119, 3119, 9811, 9812, 9411, 9412, 9413, 9421, 9422])
def test_drill_and_lifecycle_records_are_not_fire(code):
    assert not LiviSenseCoordinator._is_alarm_record({"event": {"code": code}})


@pytest.mark.parametrize("code", [1110, 1111, 9999])
def test_real_and_unknown_alarm_records_remain_eligible(code):
    assert LiviSenseCoordinator._is_alarm_record({"event": {"code": code}})


def test_unacknowledged_drill_does_not_activate_detectors(coordinator):
    coordinator.alarm_events = {"hub": [{"event": {"code": 1119}, "hub": {"id": "hub"}}]}
    assert coordinator.alarm_state("hub", "smoke") is False
    assert coordinator.alarm_state("hub", "heat") is False


def test_unacknowledged_fire_activates_detectors(coordinator):
    coordinator.alarm_events = {"hub": [{"event": {"code": 1111}, "hub": {"id": "hub"}}]}
    assert coordinator.alarm_state("hub", "smoke") is True
    assert coordinator.alarm_state("hub", "heat") is True


def test_acknowledgement_does_not_clear_real_fire(coordinator):
    coordinator.alarm_events = {"hub": [
        {"event": {"code": 1111}, "hub": {"id": "hub"}},
        {"event": {"code": 9411}, "hub": {"id": "hub"}},
    ]}
    assert coordinator.alarm_state("hub", "smoke") is True
