"""Unit tests for AURA facade M27 self-healing methods."""

from unittest.mock import MagicMock
import pytest

from app.aura import AURA
from core.fault_types import (
    ConfidenceLevel,
    FaultCategory,
    FaultDiagnosticReport,
    HealingStatus,
    RemediationPlan,
    SelfHealingResult,
)


def test_aura_facade_m27_methods():
    mock_orchestrator = MagicMock()
    mock_runtime = MagicMock()
    aura = AURA(orchestrator=mock_orchestrator, agentic_runtime=mock_runtime)

    # 1. diagnose_failure
    mock_runtime.diagnose_failure.return_value = "report"
    rep = aura.diagnose_failure("c1", "p1", "g1", "err")
    assert rep == "report"
    mock_runtime.diagnose_failure.assert_called_once()

    # 2. plan_remediation
    mock_runtime.plan_remediation.return_value = "plan"
    plan = aura.plan_remediation("report")
    assert plan == "plan"
    mock_runtime.plan_remediation.assert_called_once()

    # 3. remediate_phase
    mock_runtime.heal_campaign_phase.return_value = "healing_result"
    res = aura.remediate_phase("c1", "p1", "g1", "err")
    assert res == "healing_result"
    mock_runtime.heal_campaign_phase.assert_called_once()

    # 4. get_healing_history
    mock_runtime.get_healing_history.return_value = ["h1"]
    hist = aura.get_healing_history("c1")
    assert hist == ["h1"]

    # 5. get_healing_attempt_count
    mock_runtime.get_healing_attempt_count.return_value = 2
    cnt = aura.get_healing_attempt_count("c1", "p1")
    assert cnt == 2


def test_aura_facade_m27_runtime_not_configured():
    mock_orchestrator = MagicMock()
    mock_orchestrator.agentic_runtime = None
    aura = AURA(orchestrator=mock_orchestrator, agentic_runtime=None)

    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        aura.diagnose_failure("c1", "p1", "g1", "err")

    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        aura.plan_remediation("report")

    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        aura.remediate_phase("c1", "p1", "g1", "err")

    assert aura.get_healing_history("c1") == []
    assert aura.get_healing_attempt_count("c1", "p1") == 0
