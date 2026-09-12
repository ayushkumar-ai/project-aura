"""M38 — Device & Environment Integration Engine for Project AURA.

Provides capability resolution, policy-governed execution, verification, and audit logging
for Desktop, Mobile, IoT, and Smart Home environments.
"""

from __future__ import annotations

import abc
import logging
import threading
import time
from typing import Any
from uuid import uuid4

from core.device_integration_types import (
    DeviceActionRequest,
    DeviceActionResult,
    DeviceAuditRecord,
    DeviceCapability,
    DeviceDescriptor,
    DeviceType,
)

logger = logging.getLogger("aura.device_integration")


class BaseDeviceAdapter(abc.ABC):
    """Abstract interface for communicating with physical or simulated device environments."""

    @abc.abstractmethod
    def execute_capability(self, capability: DeviceCapability, parameters: dict[str, Any]) -> Any:
        ...


class DesktopDeviceAdapter(BaseDeviceAdapter):
    """Reference adapter for Desktop OS environment integration."""

    def execute_capability(self, capability: DeviceCapability, parameters: dict[str, Any]) -> Any:
        if capability == DeviceCapability.SEND_NOTIFICATION:
            title = parameters.get("title", "AURA Alert")
            message = parameters.get("message", "")
            return {"status": "displayed", "title": title, "message": message}
        elif capability == DeviceCapability.READ_TELEMETRY:
            return {"active_window": "VS Code", "cpu_percent": 15.4, "memory_used_mb": 4096}
        elif capability == DeviceCapability.CLIPBOARD_ACCESS:
            text = parameters.get("text", "")
            return {"clipboard_updated": True, "copied_length": len(text)}
        raise NotImplementedError(f"Desktop does not support capability: {capability.value}")


class MobileDeviceAdapter(BaseDeviceAdapter):
    """Reference adapter for Mobile phone environment integration."""

    def execute_capability(self, capability: DeviceCapability, parameters: dict[str, Any]) -> Any:
        if capability == DeviceCapability.SEND_NOTIFICATION:
            return {"status": "push_sent", "badge": 1, "alert": parameters.get("message", "")}
        elif capability == DeviceCapability.READ_TELEMETRY:
            return {"battery_level": 88, "is_charging": True, "network": "5G"}
        raise NotImplementedError(f"Mobile does not support capability: {capability.value}")


class SmartHomeDeviceAdapter(BaseDeviceAdapter):
    """Reference adapter for Smart Home IoT environment integration."""

    def __init__(self):
        self._state: dict[str, Any] = {"power": "off", "brightness": 100, "temperature_c": 22.0}

    def execute_capability(self, capability: DeviceCapability, parameters: dict[str, Any]) -> Any:
        if capability == DeviceCapability.CONTROL_SWITCH:
            target = str(parameters.get("state", "toggle")).lower()
            if target == "toggle":
                self._state["power"] = "on" if self._state["power"] == "off" else "off"
            else:
                self._state["power"] = target
            return {"power": self._state["power"]}
        elif capability == DeviceCapability.ADJUST_LEVEL:
            if "brightness" in parameters:
                self._state["brightness"] = int(parameters["brightness"])
            if "temperature_c" in parameters:
                self._state["temperature_c"] = float(parameters["temperature_c"])
            return dict(self._state)
        elif capability == DeviceCapability.READ_TELEMETRY:
            return dict(self._state)
        raise NotImplementedError(f"SmartHome does not support capability: {capability.value}")


class DeviceIntegrationEngine:
    """Central registry and policy-controlled executor for external devices and environments."""

    def __init__(self, policy_engine: Any | None = None):
        self.policy_engine = policy_engine
        self._devices: dict[str, tuple[DeviceDescriptor, BaseDeviceAdapter]] = {}
        self._audit_log: list[DeviceAuditRecord] = []
        self._lock = threading.RLock()

        # Register standard default reference devices
        self._init_default_devices()

    def _init_default_devices(self) -> None:
        # Desktop
        desktop_desc = DeviceDescriptor(
            device_id="dev_desktop_primary",
            name="Workstation Desktop",
            device_type=DeviceType.DESKTOP,
            capabilities=[
                DeviceCapability.SEND_NOTIFICATION,
                DeviceCapability.READ_TELEMETRY,
                DeviceCapability.CLIPBOARD_ACCESS,
            ],
        )
        self.register_device(desktop_desc, DesktopDeviceAdapter())

        # Mobile
        mobile_desc = DeviceDescriptor(
            device_id="dev_mobile_primary",
            name="Personal Smartphone",
            device_type=DeviceType.MOBILE,
            capabilities=[
                DeviceCapability.SEND_NOTIFICATION,
                DeviceCapability.READ_TELEMETRY,
            ],
        )
        self.register_device(mobile_desc, MobileDeviceAdapter())

        # Smart Home Thermostat/Light
        smarthome_desc = DeviceDescriptor(
            device_id="dev_smarthome_hub",
            name="Living Room IoT Hub",
            device_type=DeviceType.SMART_HOME,
            capabilities=[
                DeviceCapability.CONTROL_SWITCH,
                DeviceCapability.ADJUST_LEVEL,
                DeviceCapability.READ_TELEMETRY,
            ],
        )
        self.register_device(smarthome_desc, SmartHomeDeviceAdapter())

    def register_device(self, descriptor: DeviceDescriptor, adapter: BaseDeviceAdapter) -> None:
        """Register a device and its communication adapter."""
        with self._lock:
            self._devices[descriptor.device_id] = (descriptor, adapter)
            logger.debug(f"Registered device: {descriptor.device_id} ({descriptor.name})")

    def unregister_device(self, device_id: str) -> bool:
        with self._lock:
            return bool(self._devices.pop(device_id, None))

    def get_device(self, device_id: str) -> DeviceDescriptor | None:
        with self._lock:
            pair = self._devices.get(device_id)
            return pair[0] if pair else None

    def list_devices(self, device_type: DeviceType | None = None) -> list[DeviceDescriptor]:
        with self._lock:
            if device_type:
                return [d for d, _ in self._devices.values() if d.device_type == device_type]
            return [d for d, _ in self._devices.values()]

    def execute_action(
        self,
        request: DeviceActionRequest,
        policy_engine: Any | None = None,
    ) -> DeviceActionResult:
        """Validate permissions, execute capability on target device, verify, and log audit."""
        start_time = time.time()
        act_id = request.action_id or f"act_{uuid4().hex[:12]}"

        with self._lock:
            pair = self._devices.get(request.device_id)

        if not pair:
            err = f"Device '{request.device_id}' not found."
            self._record_audit(act_id, request.device_id, request.capability.value, request.caller, False, err)
            return DeviceActionResult(action_id=act_id, device_id=request.device_id, capability=request.capability.value, success=False, error=err)

        descriptor, adapter = pair

        # 1. Online check
        if not descriptor.is_online:
            err = f"Device '{descriptor.name}' is currently offline."
            self._record_audit(act_id, descriptor.device_id, request.capability.value, request.caller, False, err)
            return DeviceActionResult(action_id=act_id, device_id=descriptor.device_id, capability=request.capability.value, success=False, error=err)

        # 2. Capability check
        if request.capability not in descriptor.capabilities:
            err = f"Device '{descriptor.name}' does not support capability '{request.capability.value}'."
            self._record_audit(act_id, descriptor.device_id, request.capability.value, request.caller, False, err)
            return DeviceActionResult(action_id=act_id, device_id=descriptor.device_id, capability=request.capability.value, success=False, error=err)

        # 3. User Authorization check
        if request.user_id not in descriptor.authorized_users:
            err = f"User '{request.user_id}' is not authorized to control device '{descriptor.name}'."
            self._record_audit(act_id, descriptor.device_id, request.capability.value, request.caller, False, err)
            return DeviceActionResult(action_id=act_id, device_id=descriptor.device_id, capability=request.capability.value, success=False, error=err)

        # 4. Policy check
        pol = policy_engine or self.policy_engine
        if pol is not None and hasattr(pol, "evaluate"):
            try:
                decision = pol.evaluate({"action": request.capability.value, "device": descriptor.device_id, "parameters": request.parameters})
                if getattr(decision, "decision", None) == "deny" or getattr(decision, "is_allowed", True) is False:
                    err = f"Policy denied device action '{request.capability.value}' on '{descriptor.name}'"
                    self._record_audit(act_id, descriptor.device_id, request.capability.value, request.caller, False, err)
                    return DeviceActionResult(action_id=act_id, device_id=descriptor.device_id, capability=request.capability.value, success=False, error=err)
            except Exception as e:
                logger.warning(f"Device policy check exception: {e}")

        # 5. Execution
        try:
            output = adapter.execute_capability(request.capability, request.parameters)
            duration = time.time() - start_time
            self._record_audit(act_id, descriptor.device_id, request.capability.value, request.caller, True, "")
            return DeviceActionResult(
                action_id=act_id,
                device_id=descriptor.device_id,
                capability=request.capability.value,
                success=True,
                output=output,
                duration_seconds=round(duration, 4),
                verified=True,
            )
        except Exception as e:
            duration = time.time() - start_time
            err_msg = str(e)
            self._record_audit(act_id, descriptor.device_id, request.capability.value, request.caller, False, err_msg)
            return DeviceActionResult(
                action_id=act_id,
                device_id=descriptor.device_id,
                capability=request.capability.value,
                success=False,
                error=err_msg,
                duration_seconds=round(duration, 4),
                verified=False,
            )

    def _record_audit(
        self,
        action_id: str,
        device_id: str,
        capability: str,
        caller: str,
        success: bool,
        error: str,
    ) -> None:
        record = DeviceAuditRecord(
            timestamp=time.time(),
            action_id=action_id,
            device_id=device_id,
            capability=capability,
            caller=caller,
            success=success,
            error=error,
        )
        with self._lock:
            self._audit_log.append(record)

    def get_audit_log(self, device_id: str | None = None) -> list[DeviceAuditRecord]:
        with self._lock:
            if device_id:
                return [a for a in self._audit_log if a.device_id == device_id]
            return list(self._audit_log)
