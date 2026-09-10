import pytest
from core.approval import ApprovalGateway, ApprovalRequest, ApprovalStatus
from core.clarification_gateway import ClarificationGateway
from core.operator_bridge import OperatorBridge
from core.scheduling_types import ClarificationStatus
from core.session_types import (
    OperatorAction,
    OperatorActionType,
    StreamEventType,
)
from core.streaming_gateway import StreamingGateway


class FakeSupervisor:
    def __init__(self):
        self.paused_tasks = set()
        self.resumed_tasks = set()
        self.cancelled_tasks = set()

    def pause_task(self, task_id: str) -> bool:
        self.paused_tasks.add(task_id)
        return True

    def resume_task(self, task_id: str) -> bool:
        self.resumed_tasks.add(task_id)
        return True

    def cancel_task(self, task_id: str) -> bool:
        self.cancelled_tasks.add(task_id)
        return True


def test_operator_bridge_approval_workflow():
    app_gw = ApprovalGateway()
    stream_gw = StreamingGateway()
    bridge = OperatorBridge(approval_gateway=app_gw, streaming_gateway=stream_gw)

    sub_id, q = stream_gw.subscribe(session_id="s1")

    # Create approval request
    req = ApprovalRequest(
        approval_id="app_1",
        task_id="t1",
        plan_id="p1",
        step_id="step_1",
        skill_name="delete_database",
        reason="Requires admin authorization",
    )
    app_gw._requests[req.approval_id] = req

    # Push to stream
    bridge.push_pending_approval(req, session_id="s1")
    assert q.qsize() == 1
    ev = q.get_nowait()
    assert ev.event_type == StreamEventType.APPROVAL_REQUIRED
    assert ev.data["approval_id"] == req.approval_id

    # List pending
    pending = bridge.list_pending_approvals()
    assert len(pending) == 1
    assert pending[0]["approval_id"] == req.approval_id

    # Operator approves
    action = OperatorAction(
        session_id="s1",
        request_id=req.approval_id,
        action_type=OperatorActionType.APPROVE,
        operator_id="security_lead",
        decision_rationale="Verified database backup exists",
        metadata={"client": "admin_portal", "bypass_policy": True},
    )
    resolution = bridge.submit_action(action)
    assert resolution.success is True
    assert resolution.status == "approved"

    # Gateway should reflect approval
    stored = app_gw.get_request(req.approval_id)
    assert stored.status == ApprovalStatus.APPROVED
    assert stored.metadata.get("approver") == "security_lead"

    # Stream should receive APPROVAL_RESOLVED
    assert q.qsize() == 1
    res_ev = q.get_nowait()
    assert res_ev.event_type == StreamEventType.APPROVAL_RESOLVED
    assert res_ev.data["status"] == "approved"

    # Audit log
    audit = bridge.get_audit_log(session_id="s1")
    assert len(audit) == 1
    assert audit[0]["operator_id"] == "security_lead"
    assert "bypass_policy" not in audit[0]["metadata"]


def test_operator_bridge_rejection_workflow():
    app_gw = ApprovalGateway()
    stream_gw = StreamingGateway()
    bridge = OperatorBridge(approval_gateway=app_gw, streaming_gateway=stream_gw)

    req = ApprovalRequest(
        approval_id="app_2",
        task_id="t2",
        plan_id="p2",
        step_id="step_2",
        skill_name="format_disk",
        reason="Danger",
    )
    app_gw._requests[req.approval_id] = req

    action = OperatorAction(
        session_id="s2",
        request_id=req.approval_id,
        action_type=OperatorActionType.REJECT,
        operator_id="operator_bob",
        decision_rationale="Action rejected due to high risk",
    )
    resolution = bridge.submit_action(action)
    assert resolution.success is True
    assert resolution.status == "rejected"

    stored = app_gw.get_request(req.approval_id)
    assert stored.status == ApprovalStatus.REJECTED


def test_operator_bridge_clarification_workflow():
    clar_gw = ClarificationGateway()
    stream_gw = StreamingGateway()
    bridge = OperatorBridge(clarification_gateway=clar_gw, streaming_gateway=stream_gw)

    sub_id, q = stream_gw.subscribe(session_id="s3")

    creq = clar_gw.request_clarification(
        goal_id="g1",
        task_id="t3",
        question="Which environment should be targeted?",
        options=["staging", "production"],
    )

    bridge.push_pending_clarification(creq, session_id="s3")
    assert q.qsize() == 1
    ev = q.get_nowait()
    assert ev.event_type == StreamEventType.CLARIFICATION_REQUIRED
    assert ev.data["clarification_id"] == creq.clarification_id

    # List pending
    pending = bridge.list_pending_clarifications()
    assert len(pending) == 1

    # Operator responds
    action = OperatorAction(
        session_id="s3",
        request_id=creq.clarification_id,
        action_type=OperatorActionType.CLARIFY,
        operator_id="developer_alice",
        clarification_payload={"selected_option": "staging"},
    )
    resolution = bridge.submit_action(action)
    assert resolution.success is True
    assert resolution.status == "clarified"

    # Gateway state
    resp = clar_gw.get_response(creq.clarification_id)
    assert resp is not None
    assert resp.response_data == {"selected_option": "staging"}


def test_operator_bridge_pause_resume_abort():
    fake_sup = FakeSupervisor()
    stream_gw = StreamingGateway()
    bridge = OperatorBridge(streaming_gateway=stream_gw, supervisor=fake_sup)

    # Pause
    res_pause = bridge.submit_action(
        OperatorAction(session_id="s4", request_id="task_44", action_type=OperatorActionType.PAUSE)
    )
    assert res_pause.success is True
    assert "task_44" in fake_sup.paused_tasks

    # Resume
    res_resume = bridge.submit_action(
        OperatorAction(session_id="s4", request_id="task_44", action_type=OperatorActionType.RESUME)
    )
    assert res_resume.success is True
    assert "task_44" in fake_sup.resumed_tasks

    # Abort
    res_abort = bridge.submit_action(
        OperatorAction(session_id="s4", request_id="task_44", action_type=OperatorActionType.ABORT)
    )
    assert res_abort.success is True
    assert "task_44" in fake_sup.cancelled_tasks
