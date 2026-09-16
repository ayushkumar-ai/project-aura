"""M53 Unit Tests — ReadOnlyContextProvider Whitelist & Tenant Isolation."""

import pytest

from core.automations.context_provider import ReadOnlyContextProvider
from core.automations.types import AutomationValidationError


def test_whitelisted_keys_allowed():
    """Verify whitelisted context keys pass validation."""
    provider = ReadOnlyContextProvider()
    keys = ["system.time", "system.time.hour", "user.preferences", "user.tasks.recent"]
    provider.validate_keys(keys)
    ctx = provider.get_context("u1", keys)
    assert "system.time" in ctx
    assert "system.time.hour" in ctx


def test_unwhitelisted_keys_rejected():
    """Verify unwhitelisted keys raise AutomationValidationError."""
    provider = ReadOnlyContextProvider()
    bad_keys = ["secret.api_key", "internal.db_password", "os.env"]
    for bk in bad_keys:
        with pytest.raises(AutomationValidationError):
            provider.validate_keys([bk])
