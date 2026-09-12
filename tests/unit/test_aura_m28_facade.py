"""Unit tests for AURA facade Milestone 28 epistemic query methods."""

import pytest
from unittest.mock import MagicMock
from app.aura import AURA


def test_aura_facade_m28_query_methods():
    mock_orchestrator = MagicMock()
    mock_runtime = MagicMock()
    aura = AURA(orchestrator=mock_orchestrator, agentic_runtime=mock_runtime)

    # 1. query_knowledge_graph
    mock_runtime.query_knowledge_graph.return_value = ["ent1", "ent2"]
    res1 = aura.query_knowledge_graph("query_mock")
    assert res1 == ["ent1", "ent2"]
    mock_runtime.query_knowledge_graph.assert_called_once_with("query_mock")

    # 2. recommend_remediation
    mock_runtime.recommend_remediation.return_value = [{"recipe": "r1"}]
    res2 = aura.recommend_remediation("transient_infrastructure")
    assert res2 == [{"recipe": "r1"}]
    mock_runtime.recommend_remediation.assert_called_once()

    # 3. recommend_skills
    mock_runtime.recommend_skills.return_value = [{"skill": "s1"}]
    res3 = aura.recommend_skills(task_description="parse JSON")
    assert res3 == [{"skill": "s1"}]
    mock_runtime.recommend_skills.assert_called_once()

    # 4. recommend_role_allocation
    mock_runtime.recommend_role_allocation.return_value = [{"role": "architect"}]
    res4 = aura.recommend_role_allocation(goal_title="Design system")
    assert res4 == [{"role": "architect"}]
    mock_runtime.recommend_role_allocation.assert_called_once()

    # 5. find_proven_goal_patterns
    mock_runtime.find_proven_goal_patterns.return_value = [{"pattern": "p1"}]
    res5 = aura.find_proven_goal_patterns(goal_domain="data")
    assert res5 == [{"pattern": "p1"}]
    mock_runtime.find_proven_goal_patterns.assert_called_once()

    # 6. query_knowledge_subgraph
    mock_runtime.query_knowledge_subgraph.return_value = "subgraph_mock"
    res6 = aura.query_knowledge_subgraph(root_entity_id="e1", max_depth=2)
    assert res6 == "subgraph_mock"
    mock_runtime.query_knowledge_subgraph.assert_called_once()


def test_aura_facade_m28_runtime_not_configured():
    mock_orchestrator = MagicMock()
    mock_orchestrator.agentic_runtime = None
    aura = AURA(orchestrator=mock_orchestrator, agentic_runtime=None)

    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        aura.query_knowledge_graph("q")

    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        aura.recommend_remediation("transient_infrastructure")

    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        aura.recommend_skills("task")

    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        aura.recommend_role_allocation("goal")

    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        aura.find_proven_goal_patterns("domain")

    with pytest.raises(RuntimeError, match="Agentic runtime is not configured"):
        aura.query_knowledge_subgraph("e1")
