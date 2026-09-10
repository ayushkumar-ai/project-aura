import pytest

from core.agent_delegation import (
    CyclicDelegationError,
    DelegationContract,
    DelegationDepthExceededError,
    DelegationPolicyError,
    DelegationResult,
    DelegationStatus,
    DelegationTree,
)
from core.agent_role import AgentRole
from core.role_registry import RoleRegistry


def test_delegation_contract_creation_and_path():
    contract = DelegationContract(
        delegator_role_id="architect",
        delegatee_role_id="coder",
        task_description="Implement interface",
        max_depth=3,
    )

    assert contract.delegator_role_id == "architect"
    assert contract.delegatee_role_id == "coder"
    assert contract.current_depth == 1
    assert contract.delegation_path == ("architect",)


def test_delegation_contract_rejects_self_delegation():
    with pytest.raises(ValueError, match="Self-delegation forbidden"):
        DelegationContract(
            delegator_role_id="coder",
            delegatee_role_id="coder",
            task_description="Do work",
        )


def test_delegation_contract_detects_cycles():
    # Attempt cycle: architect -> coder -> architect
    with pytest.raises(CyclicDelegationError, match="Cyclic delegation detected"):
        DelegationContract(
            delegator_role_id="coder",
            delegatee_role_id="architect",
            task_description="Loop back",
            delegation_path=("architect", "coder"),
        )


def test_delegation_contract_depth_exceeded():
    with pytest.raises(DelegationDepthExceededError, match="Delegation depth .* exceeds maximum limit"):
        DelegationContract(
            delegator_role_id="reviewer",
            delegatee_role_id="data_analyst",
            task_description="Deep call",
            delegation_path=("architect", "coder", "reviewer"),
            max_depth=2,
        )


def test_delegation_tree_tracking_and_policy():
    tree = DelegationTree(max_active_delegations=2)
    roles = RoleRegistry()

    c1 = DelegationContract(
        delegator_role_id="architect",
        delegatee_role_id="coder",
        task_description="Build feature",
    )
    tree.register_delegation(c1, role_registry=roles)
    assert len(tree.list_active()) == 1
    assert tree.get_contract(c1.delegation_id) == c1

    # Unknown role raises DelegationPolicyError
    c_bad = DelegationContract(
        delegator_role_id="architect",
        delegatee_role_id="unknown_hacker_role",
        task_description="Attack",
    )
    with pytest.raises(DelegationPolicyError, match="not registered"):
        tree.register_delegation(c_bad, role_registry=roles)

    # Complete delegation
    res = DelegationResult(
        delegation_id=c1.delegation_id,
        delegator_role_id="architect",
        delegatee_role_id="coder",
        status=DelegationStatus.COMPLETED,
        output="Feature implemented",
    )
    tree.complete_delegation(res)
    assert len(tree.list_active()) == 0
    assert tree.get_result(c1.delegation_id) == res
