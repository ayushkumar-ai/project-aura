"""
Unit tests for Milestone 22 multi-agent checkpoint snapshot & recovery.
Tests persistence and restoration of DelegationTree contracts, AgentMessageBus mailboxes,
and RoleRegistry custom roles across runtime restarts.
"""

import pytest
import tempfile
import os
import shutil
from core.runtime_checkpoint import (
    RuntimeCheckpointManager,
    CheckpointMetadata,
)
from core.agent_delegation import DelegationTree, DelegationContract, DelegationResult, DelegationStatus
from core.agent_message_bus import AgentMessageBus
from core.agent_message_types import AgentMessage, AgentMessageType
from core.agent_role import AgentRole
from core.role_registry import RoleRegistry
from core.provenance import TaintedValue


@pytest.fixture
def temp_ckpt_dir():
    d = tempfile.mkdtemp(prefix="aura_test_m22_ckpt_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_multi_agent_checkpoint_snapshot_and_restore(temp_ckpt_dir):
    roles = RoleRegistry()
    roles.register(AgentRole(role_id="ml_engineer", name="ML Engineer", description="ML Engineer", system_prompt="You are an ML Engineer."))

    bus = AgentMessageBus()
    bus.publish(
        AgentMessage(
            sender_role_id="architect",
            recipient_role_id="ml_engineer",
            message_type=AgentMessageType.TASK_DELEGATION,
            payload={"task": "Train model", "epochs": 50},
            content="Train model",
        )
    )

    tree = DelegationTree()
    contract = DelegationContract(
        delegator_role_id="architect",
        delegatee_role_id="ml_engineer",
        task_description="Train vision transformer model",
        context={"task": "Train vision transformer model"},
    )
    tree.register_delegation(contract)
    tree.record_result(
        DelegationResult(
            delegation_id=contract.delegation_id,
            delegator_role_id="architect",
            delegatee_role_id="ml_engineer",
            output="Model trained successfully",
            result_payload={"loss": 0.012, "accuracy": 0.985},
        )
    )

    ckpt_mgr = RuntimeCheckpointManager(
        checkpoint_dir=temp_ckpt_dir,
        role_registry=roles,
        message_bus=bus,
        delegation_tree=tree,
    )

    snapshot_meta = ckpt_mgr.save_checkpoint(checkpoint_id="ckpt_m22_test_1")
    assert snapshot_meta is not None
    assert isinstance(snapshot_meta, CheckpointMetadata)
    assert snapshot_meta.checkpoint_id == "ckpt_m22_test_1"

    # Now simulate recovery into fresh instances
    fresh_roles = RoleRegistry()
    fresh_bus = AgentMessageBus()
    fresh_tree = DelegationTree()

    recovery_mgr = RuntimeCheckpointManager(
        checkpoint_dir=temp_ckpt_dir,
        role_registry=fresh_roles,
        message_bus=fresh_bus,
        delegation_tree=fresh_tree,
    )

    res = recovery_mgr.restore_latest()
    assert isinstance(res, CheckpointMetadata)
    assert res.checkpoint_id == "ckpt_m22_test_1"

    # Verify custom roles restored
    restored_role = fresh_roles.get_role("ml_engineer")
    assert restored_role is not None
    assert restored_role.name == "ML Engineer"

    # Verify message bus history and mailboxes restored
    history = fresh_bus.get_history()
    assert len(history) == 1
    assert history[0].sender_role_id == "architect"
    assert history[0].recipient_role_id == "ml_engineer"
    assert history[0].payload["epochs"] == 50

    # Verify delegation contracts restored
    restored_contract = fresh_tree.get_contract(contract.delegation_id)
    assert restored_contract is not None
    assert restored_contract.delegator_role_id == "architect"
    assert restored_contract.delegatee_role_id == "ml_engineer"
    
    result_rec = fresh_tree.get_result(contract.delegation_id)
    assert result_rec is not None
    assert result_rec.result_payload["accuracy"] == 0.985


def test_multi_agent_checkpoint_taint_isolation(temp_ckpt_dir):
    roles = RoleRegistry()
    bus = AgentMessageBus()
    bus.publish(
        AgentMessage(
            sender_role_id="crawler_agent",
            recipient_role_id="parser_agent",
            message_type=AgentMessageType.TASK_DELEGATION,
            content="Untrusted content",
            payload={
                "tainted_data": TaintedValue(raw_value="<script>alert(1)</script>", source_type="untrusted_site"),
            },
        )
    )

    ckpt_mgr = RuntimeCheckpointManager(
        checkpoint_dir=temp_ckpt_dir,
        role_registry=roles,
        message_bus=bus,
    )
    ckpt_mgr.save_checkpoint("ckpt_taint_1")

    fresh_bus = AgentMessageBus()
    fresh_roles = RoleRegistry()
    recovery_mgr = RuntimeCheckpointManager(
        checkpoint_dir=temp_ckpt_dir,
        role_registry=fresh_roles,
        message_bus=fresh_bus,
    )
    recovery_mgr.restore_latest()

    restored_msgs = fresh_bus.get_history()
    assert len(restored_msgs) == 1
    val = restored_msgs[0].payload["tainted_data"]
    assert isinstance(val, TaintedValue)
    assert val.source_type == "untrusted_site"
