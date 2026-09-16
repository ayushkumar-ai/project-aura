"""M53 Unit Tests — Three-Tier Condition Engine & AST Safety."""

import pytest

from core.automations.condition_engine import ConditionEngine, validate_predicate_ast
from core.automations.context_provider import ReadOnlyContextProvider
from core.automations.types import AutomationValidationError


def test_valid_ast_predicates():
    """Verify safe mathematical and boolean AST predicates evaluate correctly."""
    engine = ConditionEngine()
    
    # Simple literals & comparisons
    passed, meta = engine.evaluate("u1", {"tier": 1, "predicate": "1 < 2 and 5 >= 5"})
    assert passed is True

    passed, meta = engine.evaluate("u1", {"tier": 1, "predicate": "10 == 20 or 5 != 6"})
    assert passed is True

    passed, meta = engine.evaluate("u1", {"tier": 1, "predicate": "not (10 > 20)"})
    assert passed is True

    passed, meta = engine.evaluate("u1", {"tier": 1, "predicate": "10 > 20"})
    assert passed is False


def test_disallowed_ast_nodes_rejected():
    """Verify attempts to execute code, imports, or function calls are rejected."""
    malicious_predicates = [
        "__import__('os').system('ls')",
        "eval('1 + 1')",
        "exec('x = 1')",
        "open('/etc/passwd').read()",
        "[x for x in range(10)]",
        "lambda x: x + 1",
    ]
    for p in malicious_predicates:
        with pytest.raises(AutomationValidationError):
            validate_predicate_ast(p)


def test_context_integrated_predicate():
    """Verify predicate evaluation against injected context provider variables."""
    class MockContext(ReadOnlyContextProvider):
        def get_context(self, user_id: str, keys: list[str]):
            return {
                "system.time.hour": 14,
                "user.tasks.recent": {"pending": 2, "running": 1},
            }

    engine = ConditionEngine(context_provider=MockContext())
    cond = {
        "tier": 2,
        "predicate": "system_time_hour >= 12 and user_tasks_recent['pending'] < 5",
        "context_keys": ["system.time.hour", "user.tasks.recent"],
    }
    passed, meta = engine.evaluate("u1", cond)
    assert passed is True

    # Failing condition
    cond_fail = {
        "tier": 2,
        "predicate": "system_time_hour < 10",
        "context_keys": ["system.time.hour"],
    }
    passed_fail, meta_fail = engine.evaluate("u1", cond_fail)
    assert passed_fail is False


def test_tier3_llm_evaluation_fail_closed():
    """Verify Tier 3 condition fails closed if ModelGateway is missing or throws error."""
    # Missing gateway
    engine = ConditionEngine(model_gateway=None)
    passed, meta = engine.evaluate("u1", {"tier": 3, "llm_prompt": "Is the user active?"})
    assert passed is False
    assert "unavailable" in meta.get("error", "")

    # Mock gateway returning YES
    class MockModelGateway:
        def generate_response(self, prompt, user_id, **kwargs):
            class Resp:
                text = "YES"
            return Resp()

    engine_gw = ConditionEngine(model_gateway=MockModelGateway())
    passed_gw, meta_gw = engine_gw.evaluate("u1", {"tier": 3, "llm_prompt": "Should task run?"})
    assert passed_gw is True
