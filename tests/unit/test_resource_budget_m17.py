import pytest
from core.resource_budget import ResourceBudgetManager
from core.scheduling_types import ResourceQuota


def test_resource_budget_initial_allocation():
    mgr = ResourceBudgetManager(max_concurrent_goals=2, global_max_tool_calls_per_minute=10, global_max_tokens_per_minute=1000)
    res = mgr.acquire_quota("goal_1", estimated_tokens=100, estimated_tool_calls=1)
    assert res.is_granted is True
    assert res.allocated_tokens == 100
    assert res.allocated_tool_calls == 1

    util = mgr.get_global_utilization()
    assert util["active_goals_count"] == 1


def test_resource_budget_concurrent_goals_limit():
    mgr = ResourceBudgetManager(max_concurrent_goals=2)
    assert mgr.acquire_quota("g1").is_granted is True
    assert mgr.acquire_quota("g2").is_granted is True
    
    # 3rd goal rejected
    res3 = mgr.acquire_quota("g3")
    assert res3.is_granted is False
    assert "active goals limit reached" in res3.reason

    # Finish g1 and now g3 should succeed
    mgr.register_goal_finish("g1")
    assert mgr.acquire_quota("g3").is_granted is True


def test_resource_budget_per_goal_quota_exhaustion():
    quota = ResourceQuota(max_tool_calls=3, max_tokens=500)
    mgr = ResourceBudgetManager(default_goal_quota=quota)

    assert mgr.acquire_quota("goal_a", estimated_tokens=200, estimated_tool_calls=2).is_granted is True
    mgr.release_quota("goal_a", actual_tokens=200, actual_tool_calls=2)

    # Exceeding tool calls
    res_fail_tools = mgr.acquire_quota("goal_a", estimated_tokens=50, estimated_tool_calls=2)
    assert res_fail_tools.is_granted is False
    assert "tool calls quota exceeded" in res_fail_tools.reason

    # Another goal with fresh quota succeeds
    assert mgr.acquire_quota("goal_b", estimated_tokens=200, estimated_tool_calls=2).is_granted is True


def test_resource_budget_global_rate_limiting():
    mgr = ResourceBudgetManager(global_max_tool_calls_per_minute=5, global_max_tokens_per_minute=1000)
    assert mgr.acquire_quota("g1", estimated_tool_calls=3).is_granted is True
    mgr.release_quota("g1", actual_tool_calls=3)

    # 3 more calls within the same minute causes rate limit breach (3 + 3 = 6 > 5)
    res_rate = mgr.acquire_quota("g2", estimated_tool_calls=3)
    assert res_rate.is_granted is False
    assert "Global tool calls rate limit exceeded" in res_rate.reason


def test_resource_budget_sliding_window_replenishment():
    mgr = ResourceBudgetManager(global_max_tool_calls_per_minute=5)
    mgr.release_quota("g1", actual_tool_calls=5, current_time=100.0)

    # At t=120.0 (20s later), rate is still 5
    res_blocked = mgr.acquire_quota("g2", estimated_tool_calls=1, current_time=120.0)
    assert res_blocked.is_granted is False

    # At t=170.0 (70s later), old window expired
    res_ok = mgr.acquire_quota("g2", estimated_tool_calls=1, current_time=170.0)
    assert res_ok.is_granted is True


def test_resource_budget_reset():
    mgr = ResourceBudgetManager(max_concurrent_goals=1)
    mgr.acquire_quota("g1")
    assert mgr.get_global_utilization()["active_goals_count"] == 1
    mgr.reset()
    assert mgr.get_global_utilization()["active_goals_count"] == 0
