"""M35 — Proactive Assistance Engine for Project AURA.

Monitors events and system state, evaluates triggers with anti-spam cooldowns,
generates human-in-the-loop proposals, and coordinates safe proactive actions.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
from uuid import uuid4

from core.proactive_types import (
    ProactiveActionType,
    ProactiveAuditEntry,
    ProactiveProposal,
    ProposalStatus,
    TriggerDefinition,
    TriggerType,
)

logger = logging.getLogger("aura.proactive_engine")


class ProactiveAssistanceEngine:
    """Bounded, policy-governed proactive assistance engine."""

    def __init__(self, policy_engine: Any | None = None):
        self.policy_engine = policy_engine
        self._triggers: dict[str, TriggerDefinition] = {}
        self._proposals: dict[str, ProactiveProposal] = {}
        self._audit_log: list[ProactiveAuditEntry] = []
        self._lock = threading.RLock()

        # Register default safe triggers
        self._init_default_triggers()

    def _init_default_triggers(self) -> None:
        self.register_trigger(
            TriggerDefinition(
                trigger_id="trig_stale_goals",
                name="Stale Goal Remediation",
                description="Detects goals that have not progressed within SLA",
                trigger_type=TriggerType.SYSTEM_STATE,
                action_type=ProactiveActionType.SUGGESTION,
                condition_predicate="stale_goals_count",
                cooldown_seconds=120.0,
                requires_user_approval=True,
                proposed_payload={"action": "replan_stale_goals"},
            )
        )
        self.register_trigger(
            TriggerDefinition(
                trigger_id="trig_periodic_cleanup",
                name="Periodic Memory Consolidation",
                description="Consolidates short-term working memories periodically",
                trigger_type=TriggerType.INTERVAL,
                action_type=ProactiveActionType.MAINTENANCE,
                interval_seconds=3600.0,
                cooldown_seconds=300.0,
                requires_user_approval=False,  # Safe maintenance action
                proposed_payload={"action": "consolidate_memory"},
            )
        )

    def register_trigger(self, trigger: TriggerDefinition) -> None:
        """Register a new proactive trigger."""
        with self._lock:
            self._triggers[trigger.trigger_id] = trigger
            logger.debug(f"Registered proactive trigger: {trigger.trigger_id} ({trigger.name})")

    def unregister_trigger(self, trigger_id: str) -> bool:
        with self._lock:
            return bool(self._triggers.pop(trigger_id, None))

    def list_triggers(self) -> list[TriggerDefinition]:
        with self._lock:
            return list(self._triggers.values())

    def _eval_predicate(self, predicate: str, state: dict[str, Any]) -> bool:
        """Safely check if state matches trigger predicate key."""
        if not predicate:
            return True
        val = state.get(predicate)
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return val > 0
        if val is not None:
            return bool(val)
        return False

    def evaluate_triggers(
        self,
        current_state: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> list[ProactiveProposal]:
        """Evaluate all active triggers and generate proposals."""
        current_time = now if now is not None else time.time()
        state = current_state or {}
        new_proposals: list[ProactiveProposal] = []

        with self._lock:
            for trig in self._triggers.values():
                if not trig.enabled:
                    continue

                # 1. Cooldown & Anti-Spam Check
                if (current_time - trig.last_fired_at) < trig.cooldown_seconds:
                    continue

                # 2. Trigger Type Specific Evaluation
                should_fire = False
                if trig.trigger_type == TriggerType.INTERVAL:
                    if (current_time - trig.last_fired_at) >= trig.interval_seconds:
                        should_fire = True
                elif trig.trigger_type == TriggerType.SYSTEM_STATE or trig.trigger_type == TriggerType.CONDITION_PREDICATE:
                    if self._eval_predicate(trig.condition_predicate, state):
                        should_fire = True
                elif trig.trigger_type == TriggerType.EVENT_DRIVEN:
                    if state.get("event_topic") == trig.condition_predicate:
                        should_fire = True

                if not should_fire:
                    continue

                # Update trigger state
                trig.last_fired_at = current_time
                trig.fire_count_recent += 1

                # 3. Create Proposal
                prop_id = f"prop_{uuid4().hex[:12]}"
                initial_status = (
                    ProposalStatus.PENDING_APPROVAL
                    if trig.requires_user_approval
                    else ProposalStatus.APPROVED
                )

                proposal = ProactiveProposal(
                    proposal_id=prop_id,
                    trigger_id=trig.trigger_id,
                    action_type=trig.action_type,
                    title=f"Proactive: {trig.name}",
                    description=trig.description,
                    payload=dict(trig.proposed_payload),
                    created_at=current_time,
                    status=initial_status,
                )

                # If approval is not required, auto-execute safe action
                if not trig.requires_user_approval:
                    proposal.status = ProposalStatus.EXECUTED
                    proposal.execution_result = {"status": "auto_executed", "action": trig.proposed_payload.get("action")}
                    self._record_audit(current_time, trig.trigger_id, prop_id, trig.action_type.value, "auto_executed", "Auto-executed safe proactive action")
                else:
                    self._record_audit(current_time, trig.trigger_id, prop_id, trig.action_type.value, "pending_approval", "Generated proposal awaiting human approval")

                self._proposals[prop_id] = proposal
                new_proposals.append(proposal)

        return new_proposals

    def approve_proposal(
        self,
        proposal_id: str,
        action_executor: Any | None = None,
    ) -> ProactiveProposal:
        """Approve and optionally execute a pending proactive proposal."""
        with self._lock:
            prop = self._proposals.get(proposal_id)
            if not prop:
                raise KeyError(f"Proposal '{proposal_id}' not found.")

            if prop.status != ProposalStatus.PENDING_APPROVAL:
                return prop

            prop.status = ProposalStatus.APPROVED
            prop.user_decision = "approved"

            # Execute action
            if action_executor is not None and hasattr(action_executor, "execute"):
                try:
                    res = action_executor.execute(prop.payload)
                    prop.execution_result = res
                    prop.status = ProposalStatus.EXECUTED
                except Exception as e:
                    prop.execution_result = {"error": str(e)}
            else:
                prop.status = ProposalStatus.EXECUTED
                prop.execution_result = {"status": "executed", "payload": prop.payload}

            self._record_audit(
                time.time(),
                prop.trigger_id,
                prop.proposal_id,
                prop.action_type.value,
                prop.status.value,
                "Proposal approved and executed",
            )
            return prop

    def reject_proposal(self, proposal_id: str, reason: str = "") -> ProactiveProposal:
        """Reject a pending proactive proposal."""
        with self._lock:
            prop = self._proposals.get(proposal_id)
            if not prop:
                raise KeyError(f"Proposal '{proposal_id}' not found.")

            prop.status = ProposalStatus.REJECTED
            prop.user_decision = "rejected"
            prop.decision_reason = reason

            self._record_audit(
                time.time(),
                prop.trigger_id,
                prop.proposal_id,
                prop.action_type.value,
                "rejected",
                f"Proposal rejected: {reason}",
            )
            return prop

    def list_proposals(self, status: str | None = None) -> list[ProactiveProposal]:
        with self._lock:
            if status:
                return [p for p in self._proposals.values() if p.status.value == status]
            return list(self._proposals.values())

    def _record_audit(
        self,
        timestamp: float,
        trigger_id: str,
        proposal_id: str,
        action_type: str,
        status: str,
        message: str,
    ) -> None:
        self._audit_log.append(
            ProactiveAuditEntry(
                timestamp=timestamp,
                trigger_id=trigger_id,
                proposal_id=proposal_id,
                action_type=action_type,
                status=status,
                message=message,
            )
        )

    def get_audit_log(self) -> list[ProactiveAuditEntry]:
        with self._lock:
            return list(self._audit_log)
