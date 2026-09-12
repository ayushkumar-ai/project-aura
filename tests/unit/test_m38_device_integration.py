"""Unit tests for M38 Device & Environment Integration Subsystem."""

from core.device_integration_engine import DeviceIntegrationEngine
from core.device_integration_types import (
    DeviceActionRequest,
    DeviceCapability,
    DeviceDescriptor,
    DeviceType,
)


def test_device_engine_initialization_defaults():
    engine = DeviceIntegrationEngine()
    devices = engine.list_devices()
    assert len(devices) >= 3

    dev_ids = {d.device_id for d in devices}
    assert "dev_desktop_primary" in dev_ids
    assert "dev_mobile_primary" in dev_ids
    assert "dev_smarthome_hub" in dev_ids


def test_desktop_device_action_notification():
    engine = DeviceIntegrationEngine()
    req = DeviceActionRequest(
        action_id="act_notify_1",
        device_id="dev_desktop_primary",
        capability=DeviceCapability.SEND_NOTIFICATION,
        parameters={"title": "Test Title", "message": "Test Message"},
        caller="agent",
        user_id="user",
    )
    result = engine.execute_action(req)
    assert result.success is True
    assert result.output["status"] == "displayed"
    assert result.verified is True

    audit = engine.get_audit_log(device_id="dev_desktop_primary")
    assert len(audit) >= 1
    assert audit[-1].success is True


def test_smarthome_device_switch_and_adjust():
    engine = DeviceIntegrationEngine()

    # Toggle switch
    req_switch = DeviceActionRequest(
        action_id="act_switch_1",
        device_id="dev_smarthome_hub",
        capability=DeviceCapability.CONTROL_SWITCH,
        parameters={"state": "on"},
    )
    res_switch = engine.execute_action(req_switch)
    assert res_switch.success is True
    assert res_switch.output["power"] == "on"

    # Adjust temperature
    req_adjust = DeviceActionRequest(
        action_id="act_adjust_1",
        device_id="dev_smarthome_hub",
        capability=DeviceCapability.ADJUST_LEVEL,
        parameters={"temperature_c": 24.5, "brightness": 80},
    )
    res_adjust = engine.execute_action(req_adjust)
    assert res_adjust.success is True
    assert res_adjust.output["temperature_c"] == 24.5
    assert res_adjust.output["brightness"] == 80


def test_unsupported_capability_rejection():
    engine = DeviceIntegrationEngine()
    # Desktop does not support CONTROL_SWITCH
    req = DeviceActionRequest(
        action_id="act_invalid_1",
        device_id="dev_desktop_primary",
        capability=DeviceCapability.CONTROL_SWITCH,
        parameters={"state": "on"},
    )
    result = engine.execute_action(req)
    assert result.success is False
    assert "does not support capability" in result.error
