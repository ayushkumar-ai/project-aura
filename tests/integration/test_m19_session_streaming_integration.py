import time
import pytest
from tempfile import TemporaryDirectory

from app.aura import AURA
from core.agentic_runtime import AgenticRuntime, ExecutionMode
from core.approval import ApprovalGateway, ApprovalRequest, ApprovalStatus
from core.clarification_gateway import ClarificationGateway
from core.history import ConversationHistory
from core.orchestrator import Orchestrator
from core.policy import Policy
from core.provenance import wrap_tainted
from core.session_manager import SessionManager
from core.session_store import FileSessionStore
from core.session_types import (
    OperatorAction,
    OperatorActionType,
    SessionStatus,
    StreamEventType,
)
from core.streaming_gateway import StreamingGateway
from providers.fake_model import FakeModelProvider


def test_m19_end_to_end_session_streaming_and_operator_lifecycle():
    with TemporaryDirectory() as tmp_dir:
        store = FileSessionStore(tmp_dir)
        session_mgr = SessionManager(store=store)
        stream_gw = StreamingGateway()
        approval_gw = ApprovalGateway()
        clarification_gw = ClarificationGateway()
        model = FakeModelProvider()
        policy = Policy()

        runtime = AgenticRuntime(
            model=model,
            session_store=store,
            session_manager=session_mgr,
            streaming_gateway=stream_gw,
            approval_gateway=approval_gw,
            clarification_gateway=clarification_gw,
        )
        orch = Orchestrator(model=model, policy=policy, history=ConversationHistory())
        aura = AURA(orchestrator=orch, agentic_runtime=runtime)

        # 1. Create Session A & Session B
        sess_a = aura.create_session("session_alpha", user_id="alice", metadata={"env": "prod"})
        sess_b = aura.create_session("session_beta", user_id="bob", metadata={"env": "dev"})
        assert sess_a.session_id == "session_alpha"
        assert sess_b.session_id == "session_beta"

        # 2. Subscribe to Session A events
        sub_a, q_a = aura.subscribe_events(session_id="session_alpha")
        sub_b, q_b = aura.subscribe_events(session_id="session_beta")

        # 3. Stream a message in Session A
        events_a = list(aura.send_message_stream("Perform financial analysis", session_id="session_alpha"))
        assert len(events_a) >= 3
        assert q_a.qsize() >= 3
        # Session B queue should receive NOTHING from Session A
        assert q_b.qsize() == 0

        # Verify Session A conversation history is persisted
        ctx_a = aura.get_session("session_alpha")
        assert len(ctx_a.history.turns) == 1
        assert ctx_a.history.turns[0].user_input == "Perform financial analysis"

        # Verify Session B conversation history is EMPTY
        ctx_b = aura.get_session("session_beta")
        assert len(ctx_b.history.turns) == 0

        # 4. Submit goal in Session A
        goal_a = aura.submit_session_goal("Migrate database", session_id="session_alpha")
        assert goal_a.goal_id in aura.get_session_status("session_alpha")["active_goals"]
        assert goal_a.goal_id not in aura.get_session_status("session_beta")["active_goals"]

        # 5. Interactive Operator Bridge - Approval Workflow
        app_req = ApprovalRequest(
            approval_id="app_mig_1",
            task_id="task_mig",
            plan_id="plan_mig",
            step_id="step_mig_1",
            skill_name="migrate_db_schema",
            reason="Schema migration modifies live tables",
        )
        approval_gw._requests[app_req.approval_id] = app_req

        # Push to stream
        runtime.operator_bridge.push_pending_approval(app_req, session_id="session_alpha")
        
        # Check that session A subscriber received APPROVAL_REQUIRED event
        app_event = None
        while not q_a.empty():
            ev = q_a.get_nowait()
            if ev.event_type == StreamEventType.APPROVAL_REQUIRED:
                app_event = ev
                break
        assert app_event is not None
        assert app_event.data["approval_id"] == "app_mig_1"

        # Operator approves action
        res_app = aura.approve_action(
            approval_id="app_mig_1",
            session_id="session_alpha",
            operator_id="db_admin",
            rationale="Schema review passed",
        )
        assert res_app.success is True
        assert approval_gw.get_request("app_mig_1").status == ApprovalStatus.APPROVED

        # 6. Interactive Operator Bridge - Clarification Workflow
        clar_req = clarification_gw.request_clarification(
            goal_id=goal_a.goal_id,
            task_id="task_mig",
            question="Which replication strategy?",
            options=["sync", "async"],
        )
        runtime.operator_bridge.push_pending_clarification(clar_req, session_id="session_alpha")

        # Operator clarifies
        res_clar = aura.answer_session_clarification(
            clarification_id=clar_req.clarification_id,
            response_data={"replication": "async"},
            session_id="session_alpha",
            operator_id="db_admin",
        )
        assert res_clar.success is True
        assert clarification_gw.get_response(clar_req.clarification_id).response_data == {"replication": "async"}

        # 7. Persistence and Recovery across fresh runtime instance
        fresh_store = FileSessionStore(tmp_dir)
        fresh_mgr = SessionManager(store=fresh_store)
        fresh_runtime = AgenticRuntime(
            model=model,
            session_store=fresh_store,
            session_manager=fresh_mgr,
        )
        fresh_aura = AURA(orchestrator=orch, agentic_runtime=fresh_runtime)

        recovered_a = fresh_aura.get_session("session_alpha")
        assert recovered_a is not None
        assert recovered_a.metadata.user_id == "alice"
        assert len(recovered_a.history.turns) == 1
        assert recovered_a.history.turns[0].user_input == "Perform financial analysis"
        assert goal_a.goal_id in recovered_a.active_goal_ids

        # Clean close
        assert fresh_aura.close_session("session_alpha") is True
        assert fresh_aura.get_session_status("session_alpha")["status"] == "completed"
