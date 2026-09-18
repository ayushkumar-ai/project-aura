"""M58 — Platform Adapters Package."""

from core.platform.adapters.base import BasePlatformAdapter
from core.platform.adapters.desktop import DesktopOSAdapter
from core.platform.adapters.simulated import SimulatedPlatformAdapter

__all__ = [
    "BasePlatformAdapter",
    "DesktopOSAdapter",
    "SimulatedPlatformAdapter",
]
