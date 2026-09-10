import pytest
from core.clarification_gateway import ClarificationGateway
from core.scheduling_types import ClarificationStatus, ClarificationType
from core.provenance import wrap_tainted


def test_clarification_request_creation():
    gw = ClarificationGateway(default_timeout_seconds=300.0)
    req = gw.request_clarification(
        goal_id="goal_1",
        task_id="task_1_act_1",
        question="Which database table should be purged?",
        options=["users", "logs", "sessions"],
        clarification_type=ClarificationType.SINGLE_CHOICE,
    )
    assert req.goal_id == "goal_1"
    assert req.question == "Which database table should be purged?"
    assert req.options == ("users", "logs", "sessions")
    assert req.status == ClarificationStatus.PENDING

    pending = gw.get_pending_requests()
    assert len(pending) == 1
    assert pending[0].clarification_id == req.clarification_id


def test_clarification_submit_response():
    gw = ClarificationGateway()
    req = gw.request_clarification(
        goal_id="goal_1",
        task_id="task_1",
        question="Confirm operation?",
        options=["yes", "no"],
    )

    resp = gw.submit_response(req.clarification_id, "yes")
    assert resp.clarification_id == req.clarification_id
    assert resp.response_data == "yes"
    assert resp.status == ClarificationStatus.ANSWERED

    # Request is no longer pending
    assert len(gw.get_pending_requests()) == 0
    updated_req = gw.get_request(req.clarification_id)
    assert updated_req.status == ClarificationStatus.ANSWERED


def test_clarification_timeout():
    gw = ClarificationGateway(default_timeout_seconds=50.0)
    req = gw.request_clarification(
        goal_id="goal_1",
        task_id="task_1",
        question="Select option",
        current_time=100.0,
    )

    # At t=120.0, still pending
    assert len(gw.get_pending_requests(current_time=120.0)) == 1

    # At t=160.0, expired
    with pytest.raises(ValueError, match="timed out"):
        gw.submit_response(req.clarification_id, "val", current_time=160.0)

    assert len(gw.get_pending_requests()) == 0


def test_clarification_cancellation():
    gw = ClarificationGateway()
    req = gw.request_clarification(goal_id="g1", task_id="t1", question="Q?")
    assert gw.cancel_request(req.clarification_id, reason="Goal aborted") is True

    updated = gw.get_request(req.clarification_id)
    assert updated.status == ClarificationStatus.CANCELLED
    assert updated.metadata["cancel_reason"] == "Goal aborted"


def test_clarification_taint_propagation():
    gw = ClarificationGateway()
    req = gw.request_clarification(goal_id="g1", task_id="t1", question="Input?")

    tainted_input = wrap_tainted("untrusted_user_text", is_untrusted=True, source_type="user_form")
    resp = gw.submit_response(req.clarification_id, tainted_input)
    assert resp.is_untrusted is True


def test_invalid_and_non_pending_submissions():
    gw = ClarificationGateway()
    with pytest.raises(KeyError):
        gw.submit_response("non_existent_id", "foo")

    req = gw.request_clarification(goal_id="g1", task_id="t1", question="Q?")
    gw.submit_response(req.clarification_id, "ans1")

    # Second submission on already answered request
    with pytest.raises(ValueError, match="is not pending"):
        gw.submit_response(req.clarification_id, "ans2")
