"""Unit tests for M39 Cross-Device AURA State Synchronization Subsystem."""

from core.cross_device_sync_engine import CrossDeviceSyncEngine
from core.cross_device_types import (
    ConflictResolutionStrategy,
    SyncDelta,
    SyncOperationType,
    VectorClock,
)


def test_vector_clock_causality():
    v1 = VectorClock({"dev_a": 1, "dev_b": 0})
    v2 = VectorClock({"dev_a": 1, "dev_b": 1})

    assert v2.is_causally_newer(v1) is True
    assert v1.is_causally_newer(v2) is False

    v1.increment("dev_a")  # now {"dev_a": 2, "dev_b": 0}
    # Concurrent clocks
    assert v1.is_causally_newer(v2) is False
    assert v2.is_causally_newer(v1) is False

    v1.merge(v2)
    assert v1.to_dict() == {"dev_a": 2, "dev_b": 1}


def test_cross_device_peer_synchronization():
    node_desktop = CrossDeviceSyncEngine(device_id="desktop_node")
    node_mobile = CrossDeviceSyncEngine(device_id="mobile_node")

    # Generate delta on desktop
    delta_pref = node_desktop.generate_delta(
        operation=SyncOperationType.SET_PREFERENCE,
        entity_id="pref_theme",
        payload={"theme": "dark_mode", "updated_by": "desktop"},
    )
    assert delta_pref.source_device_id == "desktop_node"

    # Sync peer nodes
    synced = node_desktop.sync_with_peer(node_mobile)
    assert synced >= 1

    # Verify mobile node received state
    mobile_val = node_mobile.get_cached_entity("pref_theme")
    assert mobile_val is not None
    assert mobile_val["payload"]["theme"] == "dark_mode"
    assert node_mobile.get_status().vector_clock.get("desktop_node") == 1


def test_idempotent_duplicate_rejection():
    node = CrossDeviceSyncEngine(device_id="node_1")
    delta = SyncDelta(
        delta_id="d1",
        source_device_id="node_2",
        operation=SyncOperationType.UPDATE_TASK_STATE,
        entity_id="task_123",
        payload={"status": "completed"},
        idempotency_key="unique_idem_key_999",
    )

    # First application
    applied1, msg1 = node.receive_delta(delta)
    assert applied1 is True
    assert msg1 == "applied"

    # Duplicate application with same idempotency key
    applied2, msg2 = node.receive_delta(delta)
    assert applied2 is True
    assert msg2 == "already_applied_idempotent"
    assert node.get_status().applied_deltas_count == 1
