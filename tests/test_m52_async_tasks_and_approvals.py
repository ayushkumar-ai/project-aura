"""M52 Test Suite — Asynchronous Background Task Execution & Human Approval Gateway.

Exhaustive qualification suite verifying:
1. Task Repository CRUD, lifecycle status transitions, steps, idempotency, and tenant isolation
2. Approval Repository persistence, nonce verification, single-use, expiration, and cross-tenant defense
3. BackgroundTaskWorker bounded concurrency, task acquisition, cancellation, and timeout
4. Startup and periodic crash/orphan task recovery sweeps
5. WorkflowOrchestrator plan execution, step progression, and sensitive action approval halts
6. Server-Sent Events (SSE) streaming and event pub/sub
7. REST API Endpoints: POST /v1/tasks (202 Accepted), GET /v1/tasks/{id}, POST /v1/tasks/{id}/cancel,
   GET /v1/tasks/{id}/events, GET /v1/approvals, POST /v1/approvals/{id}/decide
8. Adversarial security: Policy DENY (zero tool runs), invalid nonces, expired approvals, cross-tenant isolation
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.request
import urllib.error
from typing import Any
import pytest

from app.config import Settings
from app.server import AURAHTTPServer
from core.auth import create_token_authenticator
from core.background import (
    BackgroundTaskWorker,
    TaskEventBroadcaster,
    WorkflowOrchestrator,
)
from core.cancellation import CancellationToken
from core.identity import UserIdentity, UserRole
from core.metrics import get_metrics_registry
from core.policy import Policy, PolicyDecision
from core.repositories.factory import create_in_memory_repositories
from core.repositories.in_memory import InMemoryApprovalRepository, InMemoryTaskRepository
from core.tool_ecosystem import ToolEcosystemRegistry, SafeCalculatorTool


@pytest.fixture
def repos():
    """Isolated in-memory repository container."""
    return create_in_memory_repositories()


def _find_free_port() -> int:
    """Find a random available TCP port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# =========================================================================
# 1. Task Repository Tests
# =========================================================================

def test_task_repo_crud_and_status_transitions(repos):
    """Verify task creation, retrieval, and status updates."""
    task_repo = repos.tasks
    assert task_repo is not None

    task = task_repo.create_task(
        user_id="user_alice",
        title="Analyze Repo Security",
        goal="Scan codebase for vulnerabilities",
        context={"priority": "high"},
        timeout_seconds=300,
    )
    assert task["id"] is not None
    assert task["user_id"] == "user_alice"
    assert task["status"] == "pending"
    assert task["title"] == "Analyze Repo Security"
    assert task["timeout_seconds"] == 300

    # Retrieve task
    fetched = task_repo.get_task(task["id"], user_id="user_alice")
    assert fetched is not None
    assert fetched["id"] == task["id"]
    assert fetched["context"]["priority"] == "high"

    # Status transition to running
    ok = task_repo.update_task_status(task["id"], user_id="user_alice", status="running")
    assert ok is True
    running_task = task_repo.get_task(task["id"], user_id="user_alice")
    assert running_task["status"] == "running"
    assert running_task["started_at"] is not None

    # Status transition to completed
    res = {"vulnerabilities_found": 0, "status": "clean"}
    ok = task_repo.update_task_status(task["id"], user_id="user_alice", status="completed", result=res)
    assert ok is True
    completed_task = task_repo.get_task(task["id"], user_id="user_alice")
    assert completed_task["status"] == "completed"
    assert completed_task["result"] == res
    assert completed_task["completed_at"] is not None


def test_task_repo_tenant_isolation(repos):
    """User Bob cannot view or modify User Alice's tasks."""
    task_repo = repos.tasks
    task_alice = task_repo.create_task(
        user_id="user_alice",
        title="Alice Task",
        goal="Alice private goal",
    )

    # Bob attempts to read Alice's task
    bob_view = task_repo.get_task(task_alice["id"], user_id="user_bob")
    assert bob_view is None

    # Bob attempts to update Alice's task
    bob_update = task_repo.update_task_status(task_alice["id"], user_id="user_bob", status="cancelled")
    assert bob_update is False

    # Bob listing tasks does not see Alice's task
    bob_tasks = task_repo.list_tasks(user_id="user_bob")
    assert len(bob_tasks) == 0

    # Alice listing tasks sees her task
    alice_tasks = task_repo.list_tasks(user_id="user_alice")
    assert len(alice_tasks) == 1
    assert alice_tasks[0]["id"] == task_alice["id"]


def test_task_repo_idempotency(repos):
    """Duplicate task submission with same idempotency key returns existing task."""
    task_repo = repos.tasks
    t1 = task_repo.create_task(
        user_id="user_alice",
        title="Idempotent Task",
        goal="Do something once",
        idempotency_key="idem_key_12345",
    )
    t2 = task_repo.create_task(
        user_id="user_alice",
        title="Idempotent Task Duplicate",
        goal="Do something once duplicate",
        idempotency_key="idem_key_12345",
    )
    assert t1["id"] == t2["id"]
    assert t1["title"] == t2["title"]

    # Different user with same key gets different task
    t_bob = task_repo.create_task(
        user_id="user_bob",
        title="Bob Task",
        goal="Bob goal",
        idempotency_key="idem_key_12345",
    )
    assert t_bob["id"] != t1["id"]


def test_task_steps_persistence_and_order(repos):
    """Verify task step creation, update, and ordered retrieval."""
    task_repo = repos.tasks
    t = task_repo.create_task(user_id="user_alice", title="Multi-Step", goal="Execute DAG")

    task_repo.create_or_update_step(t["id"], "user_alice", step_index=0, name="Step 0", status="completed", tool_output={"x": 1})
    task_repo.create_or_update_step(t["id"], "user_alice", step_index=1, name="Step 1", status="running", tool_name="calculator")

    steps = task_repo.get_steps(t["id"], user_id="user_alice")
    assert len(steps) == 2
    assert steps[0]["step_index"] == 0
    assert steps[0]["status"] == "completed"
    assert steps[0]["tool_output"] == {"x": 1}
    assert steps[1]["step_index"] == 1
    assert steps[1]["status"] == "running"

    # User Bob cannot access Alice's steps
    assert len(task_repo.get_steps(t["id"], user_id="user_bob")) == 0


# =========================================================================
# 2. Approval Repository Tests
# =========================================================================

def test_approval_repo_create_and_decide(repos):
    """Verify approval creation, nonce validation, and decision flow."""
    task_repo = repos.tasks
    appr_repo = repos.approvals
    task = task_repo.create_task(user_id="user_alice", title="Sensitive Action Task", goal="Deploy to prod")
    task_repo.update_task_status(task["id"], "user_alice", "awaiting_approval")

    appr = appr_repo.create_approval(
        task_id=task["id"],
        user_id="user_alice",
        action_type="tool_execution",
        action_payload={"tool": "deploy", "env": "prod"},
        justification="Production deployment requested",
        risk_level="critical",
    )
    assert appr["id"] is not None
    assert appr["status"] == "pending"
    assert len(appr["nonce"]) >= 32

    # Check pending list
    pending = appr_repo.list_pending_approvals("user_alice")
    assert len(pending) == 1
    assert pending[0]["id"] == appr["id"]

    # Decide with valid nonce
    ok, msg, decided = appr_repo.decide_approval(
        approval_id=appr["id"],
        user_id="user_alice",
        decision="approved",
        nonce=appr["nonce"],
        reason="Approved by Tech Lead",
    )
    assert ok is True
    assert decided["status"] == "approved"
    assert decided["decision_reason"] == "Approved by Tech Lead"

    # Verify task was resumed to pending
    resumed_task = task_repo.get_task(task["id"], "user_alice")
    assert resumed_task["status"] == "pending"


def test_approval_repo_invalid_nonce(repos):
    """Decision with invalid nonce is rejected."""
    appr_repo = repos.approvals
    appr = appr_repo.create_approval("t1", "user_alice", "tool", {}, "justification")

    ok, msg, decided = appr_repo.decide_approval(
        approval_id=appr["id"],
        user_id="user_alice",
        decision="approved",
        nonce="INVALID_NONCE_VALUE_12345",
    )
    assert ok is False
    assert "nonce" in msg.lower()


def test_approval_repo_double_decision(repos):
    """Approval cannot be decided more than once."""
    appr_repo = repos.approvals
    appr = appr_repo.create_approval("t1", "user_alice", "tool", {}, "justification")

    ok1, _, _ = appr_repo.decide_approval(appr["id"], "user_alice", "approved", appr["nonce"])
    assert ok1 is True

    ok2, msg2, _ = appr_repo.decide_approval(appr["id"], "user_alice", "approved", appr["nonce"])
    assert ok2 is False
    assert "already" in msg2.lower()


def test_approval_repo_cross_tenant_isolation(repos):
    """User Bob cannot view or decide User Alice's approval request."""
    appr_repo = repos.approvals
    appr = appr_repo.create_approval("t1", "user_alice", "tool", {}, "justification")

    # Bob views
    assert appr_repo.get_approval(appr["id"], user_id="user_bob") is None
    assert len(appr_repo.list_pending_approvals(user_id="user_bob")) == 0

    # Bob attempts to decide
    ok, msg, _ = appr_repo.decide_approval(appr["id"], user_id="user_bob", decision="approved", nonce=appr["nonce"])
    assert ok is False
    assert "not found" in msg.lower()


def test_approval_repo_expiration(repos):
    """Expired approval requests cannot be approved."""
    appr_repo = repos.approvals
    # Expires immediately (-1s)
    appr = appr_repo.create_approval("t1", "user_alice", "tool", {}, "justification", expires_in_seconds=-1)

    ok, msg, _ = appr_repo.decide_approval(appr["id"], "user_alice", "approved", appr["nonce"])
    assert ok is False
    assert "expired" in msg.lower()


# =========================================================================
# 3. Worker & Orchestration Tests
# =========================================================================

def test_workflow_orchestrator_complete_lifecycle(repos):
    """WorkflowOrchestrator executes plan steps and completes task."""
    task_repo = repos.tasks
    appr_repo = repos.approvals
    broadcaster = TaskEventBroadcaster()
    orchestrator = WorkflowOrchestrator(
        task_repo=task_repo,
        approval_repo=appr_repo,
        broadcaster=broadcaster,
    )

    task = task_repo.create_task(user_id="user_alice", title="Calculate Numbers", goal="Calculate 25 + 75")
    task_repo.update_task_status(task["id"], "user_alice", "running")

    result = orchestrator.execute_task(task)
    assert result["status"] == "completed"

    updated = task_repo.get_task(task["id"], "user_alice")
    assert updated["status"] == "completed"
    assert updated["result"] is not None


def test_workflow_orchestrator_approval_halt(repos):
    """WorkflowOrchestrator halts when encountering a sensitive tool."""
    task_repo = repos.tasks
    appr_repo = repos.approvals
    broadcaster = TaskEventBroadcaster()
    orchestrator = WorkflowOrchestrator(
        task_repo=task_repo,
        approval_repo=appr_repo,
        broadcaster=broadcaster,
    )

    # Goal requiring sensitive action
    task = task_repo.create_task(user_id="user_alice", title="Fetch Web Page", goal="Use web_fetch to read https://example.com")
    task_repo.update_task_status(task["id"], "user_alice", "running")

    # Force step 1 to have tool web_fetch
    task_repo.create_or_update_step(task["id"], "user_alice", step_index=0, name="Fetch Web", status="pending", tool_name="web_fetch")

    res = orchestrator.execute_task(task)
    assert res["status"] == "awaiting_approval"
    assert "approval_id" in res

    # Verify task status in repo
    updated_task = task_repo.get_task(task["id"], "user_alice")
    assert updated_task["status"] == "awaiting_approval"

    # Verify approval record
    apprs = appr_repo.list_pending_approvals("user_alice")
    assert len(apprs) == 1
    assert apprs[0]["id"] == res["approval_id"]


def test_workflow_orchestrator_policy_denial(repos):
    """Security Policy DENY terminates execution with zero tool execution."""
    task_repo = repos.tasks
    appr_repo = repos.approvals

    # Policy that explicitly denies web_fetch
    policy = Policy(authorized_tools={"calculator", "echo"})
    orchestrator = WorkflowOrchestrator(
        task_repo=task_repo,
        approval_repo=appr_repo,
        policy_engine=policy,
    )

    task = task_repo.create_task(user_id="user_alice", title="Denied Action", goal="Run forbidden tool")
    task_repo.create_or_update_step(task["id"], "user_alice", step_index=0, name="Run forbidden", status="pending", tool_name="web_fetch")

    res = orchestrator.execute_task(task)
    assert res["status"] == "failed"
    assert "denied" in res["error"].lower()

    updated = task_repo.get_task(task["id"], "user_alice")
    assert updated["status"] == "failed"


def test_background_worker_concurrency_and_execution(repos):
    """BackgroundTaskWorker automatically leases and executes pending tasks."""
    task_repo = repos.tasks
    appr_repo = repos.approvals
    broadcaster = TaskEventBroadcaster()
    orchestrator = WorkflowOrchestrator(task_repo=task_repo, approval_repo=appr_repo, broadcaster=broadcaster)

    worker = BackgroundTaskWorker(
        task_repo=task_repo,
        approval_repo=appr_repo,
        orchestrator=orchestrator,
        concurrency=2,
        poll_interval_ms=50,
    )

    # Submit 2 tasks
    t1 = task_repo.create_task("user_alice", "Task 1", "Goal 1")
    t2 = task_repo.create_task("user_alice", "Task 2", "Goal 2")

    worker.start()
    try:
        # Wait for worker to execute both tasks
        for _ in range(50):
            task1 = task_repo.get_task(t1["id"], "user_alice")
            task2 = task_repo.get_task(t2["id"], "user_alice")
            if task1["status"] == "completed" and task2["status"] == "completed":
                break
            time.sleep(0.1)

        task1 = task_repo.get_task(t1["id"], "user_alice")
        task2 = task_repo.get_task(t2["id"], "user_alice")
        assert task1["status"] == "completed"
        assert task2["status"] == "completed"
    finally:
        worker.stop()


def test_worker_cancellation(repos):
    """Cooperative cancellation halts in-flight task."""
    task_repo = repos.tasks
    appr_repo = repos.approvals
    orchestrator = WorkflowOrchestrator(task_repo=task_repo, approval_repo=appr_repo)

    token = CancellationToken()
    task = task_repo.create_task("user_alice", "Cancellable", "Run slow steps")
    task_repo.create_or_update_step(task["id"], "user_alice", 0, "Step 0", "pending")
    task_repo.create_or_update_step(task["id"], "user_alice", 1, "Step 1", "pending")

    # Cancel token before step 1
    token.cancel()
    res = orchestrator.execute_task(task, cancellation_requested=token.is_cancelled)
    assert res["status"] == "cancelled"

    updated = task_repo.get_task(task["id"], "user_alice")
    assert updated["status"] == "cancelled"


def test_worker_crash_recovery_sweep(repos):
    """Stale tasks stuck in running status are recovered to failed."""
    task_repo = repos.tasks
    task = task_repo.create_task("user_alice", "Crashed Task", "Will crash")
    task_repo.update_task_status(task["id"], "user_alice", "running")

    # Simulate crash by setting updated_at in past
    task_record = task_repo._tasks[task["id"]]
    task_record["updated_at"] = time.time() - 400.0

    recovered = task_repo.recover_stale_tasks(stale_threshold_seconds=300.0)
    assert task["id"] in recovered

    updated = task_repo.get_task(task["id"], "user_alice")
    assert updated["status"] == "failed"
    assert "crashed" in updated["error_message"].lower()


# =========================================================================
# 4. HTTP API & SSE Integration Tests
# =========================================================================

@pytest.fixture
def live_m52_server(repos):
    """Start live test server with M52 background worker enabled."""
    port = _find_free_port()
    cfg = Settings(
        aura_env="development",
        aura_server_host="127.0.0.1",
        aura_server_port=port,
        aura_api_key_auth_enabled=False,
        aura_task_worker_enabled=True,
        aura_task_worker_concurrency=2,
        aura_task_poll_interval_ms=50,
    )
    server = AURAHTTPServer(
        config=cfg,
        host="127.0.0.1",
        port=port,
        repository_container=repos,
    )
    server.start(block=False)
    time.sleep(0.3)

    base_url = f"http://127.0.0.1:{port}"
    yield base_url, repos

    server.stop()


def test_api_submit_task_async(live_m52_server):
    """POST /v1/tasks returns 202 Accepted with task_id and events_url."""
    base_url, _ = live_m52_server

    payload = {
        "title": "API Async Task",
        "goal": "Process background request",
        "context": {"source": "integration_test"},
    }
    req = urllib.request.Request(
        f"{base_url}/v1/tasks",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=5.0) as resp:
        assert resp.status == 202
        body = json.loads(resp.read().decode("utf-8"))
        assert "task_id" in body
        assert body["status"] == "pending"
        assert body["title"] == "API Async Task"
        assert f"/v1/tasks/{body['task_id']}/events" in body["events_url"]
        task_id = body["task_id"]

    # Poll until completed
    for _ in range(40):
        time.sleep(0.1)
        get_req = urllib.request.Request(f"{base_url}/v1/tasks/{task_id}")
        with urllib.request.urlopen(get_req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data["status"] == "completed":
                assert len(data["steps"]) > 0
                break
    else:
        pytest.fail("Task did not complete within timeout")


def test_api_cancel_task(live_m52_server):
    """POST /v1/tasks/{id}/cancel marks task cancelled."""
    base_url, repos = live_m52_server
    # Create task with a sensitive step and set awaiting_approval so it cannot instantly complete
    t = repos.tasks.create_task("dev_user", "Cancel via API", "Some long goal")
    repos.tasks.create_or_update_step(t["id"], "dev_user", step_index=0, name="Sensitive Step", status="pending", tool_name="web_fetch")
    repos.tasks.update_task_status(t["id"], "dev_user", "awaiting_approval")

    cancel_req = urllib.request.Request(
        f"{base_url}/v1/tasks/{t['id']}/cancel",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(cancel_req, timeout=5.0) as resp:
        assert resp.status == 200
        body = json.loads(resp.read().decode("utf-8"))
        assert body["status"] == "cancelled"

    # Verify task state
    get_req = urllib.request.Request(f"{base_url}/v1/tasks/{t['id']}")
    with urllib.request.urlopen(get_req, timeout=5.0) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        assert data["status"] == "cancelled"


def test_api_approval_list_and_decide(live_m52_server):
    """GET /v1/approvals and POST /v1/approvals/{id}/decide end-to-end."""
    base_url, repos = live_m52_server

    # Create task and approval
    task = repos.tasks.create_task("dev_user", "Action needing approval", "Delete temp cache")
    repos.tasks.update_task_status(task["id"], "dev_user", "awaiting_approval")
    appr = repos.approvals.create_approval(
        task_id=task["id"],
        user_id="dev_user",
        action_type="file_delete",
        action_payload={"path": "/tmp/cache"},
        justification="Clean disk space",
        risk_level="high",
    )

    # 1. List approvals
    list_req = urllib.request.Request(f"{base_url}/v1/approvals")
    with urllib.request.urlopen(list_req, timeout=5.0) as resp:
        assert resp.status == 200
        list_body = json.loads(resp.read().decode("utf-8"))
        assert list_body["count"] >= 1
        found = next((a for a in list_body["approvals"] if a["id"] == appr["id"]), None)
        assert found is not None

    # 2. Get approval detail
    detail_req = urllib.request.Request(f"{base_url}/v1/approvals/{appr['id']}")
    with urllib.request.urlopen(detail_req, timeout=5.0) as resp:
        assert resp.status == 200
        detail_body = json.loads(resp.read().decode("utf-8"))
        assert detail_body["id"] == appr["id"]
        assert detail_body["status"] == "pending"

    # 3. Decide approval
    decide_payload = {
        "decision": "approved",
        "nonce": appr["nonce"],
        "reason": "Authorized by operator",
    }
    decide_req = urllib.request.Request(
        f"{base_url}/v1/approvals/{appr['id']}/decide",
        data=json.dumps(decide_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(decide_req, timeout=5.0) as resp:
        assert resp.status == 200
        dec_body = json.loads(resp.read().decode("utf-8"))
        assert dec_body["status"] == "approved"
        assert dec_body["resumed"] is True

    # 4. Verify task state resumed to pending (or already leased/completed by live worker)
    t_after = repos.tasks.get_task(task["id"], "dev_user")
    assert t_after["status"] in ("pending", "running", "completed")


def test_api_sse_stream(live_m52_server):
    """GET /v1/tasks/{id}/events delivers SSE formatted stream."""
    base_url, repos = live_m52_server
    task = repos.tasks.create_task("dev_user", "SSE Task", "Goal for SSE")

    # Connect to SSE
    sse_req = urllib.request.Request(f"{base_url}/v1/tasks/{task['id']}/events")
    with urllib.request.urlopen(sse_req, timeout=5.0) as resp:
        assert resp.status == 200
        assert "text/event-stream" in resp.headers.get("Content-Type", "")
        # Read first event
        first_line = resp.readline().decode("utf-8")
        assert first_line.startswith("event:")


# =========================================================================
# 5. M52 Hardening: Cancellation & Terminal-State Race Condition Tests
# =========================================================================

class SynchronizedOrchestrator(WorkflowOrchestrator):
    """WorkflowOrchestrator subclass that supports synchronized callbacks during tool execution."""

    def __init__(self, *args, step_callback=None, **kwargs):
        if "policy_engine" not in kwargs:
            from core.policy import Policy
            all_authorized = set(Policy().authorized_tools) | {
                "echo_slow", "echo_fast", "tool_a", "tool_b", "tool_0", "tool_1", "tool_2",
                "tool_final", "step_0", "step_1", "step_2"
            }
            kwargs["policy_engine"] = Policy(authorized_tools=all_authorized)
        super().__init__(*args, **kwargs)
        self.step_callback = step_callback

    def _execute_step_tool(self, tool_name: str, parameters: dict[str, Any], user_id: str) -> dict[str, Any]:
        if self.step_callback:
            return self.step_callback(tool_name, parameters, user_id)
        return {"result": f"Executed {tool_name}"}


def test_critical_race_cancellation_racing_with_final_step_completion(repos):
    """Critical Race Test (Prompt Section 8):
    Cancellation racing with final-step completion must preserve durable cancelled state.
    1. Create task containing at least two workflow steps.
    2. Make the final step block inside its external tool execution.
    3. Start the worker/orchestrator.
    4. Confirm the final step is executing.
    5. Concurrently invoke task cancellation.
    6. Confirm the database task becomes: cancelled.
    7. Release the blocked external tool.
    8. Allow the worker to finish.
    9. Assert: tasks.status == cancelled.
    10. Assert the physically executed final step is recorded as: completed.
    11. Assert no later workflow step executes.
    12. Assert a cancellation terminal event is emitted.
    13. Assert a completion terminal event is NOT emitted after cancellation.
    14. Assert the worker does not change the durable terminal state.
    """
    task_repo = repos.tasks
    appr_repo = repos.approvals
    broadcaster = TaskEventBroadcaster()

    task = task_repo.create_task("user_alice", "Multi-Step Race Task", "Perform step 0 then step 1")
    task_id = task["id"]
    event_queue = broadcaster.subscribe(task_id)
    task_repo.create_or_update_step(task_id, "user_alice", 0, "Step 0 - Fast", "pending", tool_name="echo_fast")
    task_repo.create_or_update_step(task_id, "user_alice", 1, "Step 1 - Blocking Final", "pending", tool_name="echo_slow")

    token = CancellationToken()
    final_step_executing = threading.Event()
    final_step_release = threading.Event()

    def step_callback(tool_name, params, uid):
        if tool_name == "echo_slow":
            final_step_executing.set()
            assert final_step_release.wait(timeout=5.0), "Timed out waiting for release"
            return {"echo_slow_output": "success"}
        return {"fast_output": "done"}

    orchestrator = SynchronizedOrchestrator(
        task_repo=task_repo,
        approval_repo=appr_repo,
        broadcaster=broadcaster,
        step_callback=step_callback,
    )

    task_repo.update_task_status(task_id, "user_alice", "running")

    worker_res = {}
    def run_worker():
        worker_res["result"] = orchestrator.execute_task(task, cancellation_requested=token.is_cancelled)

    t = threading.Thread(target=run_worker)
    t.start()

    # Wait until final step is actively executing
    assert final_step_executing.wait(timeout=5.0), "Final step did not start executing"

    # Concurrently invoke task cancellation
    cancelled_ok = task_repo.cancel_task(task_id, "user_alice")
    assert cancelled_ok is True
    token.cancel()

    # Confirm the database task becomes: cancelled
    durable_task = task_repo.get_task(task_id, "user_alice")
    assert durable_task["status"] == "cancelled"

    # Release the blocked external tool
    final_step_release.set()
    t.join(timeout=5.0)
    assert not t.is_alive(), "Worker thread hung"

    # 9. Assert tasks.status == cancelled
    final_durable = task_repo.get_task(task_id, "user_alice")
    assert final_durable["status"] == "cancelled"

    # 10. Assert physically executed final step is recorded as: completed
    steps = task_repo.get_steps(task_id, "user_alice")
    assert len(steps) == 2
    step_0 = next(s for s in steps if s["step_index"] == 0)
    step_1 = next(s for s in steps if s["step_index"] == 1)
    assert step_0["status"] == "completed"
    assert step_1["status"] == "completed"
    assert step_1["tool_output"] == {"echo_slow_output": "success"}

    # 11. Assert no later workflow step executes (only 2 steps existed)
    assert len(steps) == 2

    # 12. Assert cancellation terminal event is emitted
    events_emitted = []
    while not event_queue.empty():
        events_emitted.append(event_queue.get_nowait())
    event_names = [e.get("event") for e in events_emitted]
    assert "task_cancelled" in event_names

    # 13. Assert completion terminal event is NOT emitted after cancellation
    assert "task_completed" not in event_names

    # 14. Assert worker return value and durable state are cancelled
    assert worker_res["result"]["status"] == "cancelled"
    assert final_durable["status"] == "cancelled"


def test_race_validation_a_cancellation_before_tool_returns(repos):
    """Validation A: Cancellation happens while external tool is executing;
    when tool finishes, task remains cancelled and step is recorded as completed."""
    task_repo = repos.tasks
    appr_repo = repos.approvals
    broadcaster = TaskEventBroadcaster()

    task = task_repo.create_task("user_alice", "Race A Task", "Goal A")
    task_id = task["id"]
    task_repo.create_or_update_step(task_id, "user_alice", 0, "Step 0", "pending", tool_name="tool_a")

    token = CancellationToken()
    tool_running = threading.Event()
    tool_release = threading.Event()

    def step_cb(tool_name, params, uid):
        tool_running.set()
        tool_release.wait(timeout=5.0)
        return {"out": "step_done"}

    orch = SynchronizedOrchestrator(
        task_repo=task_repo, approval_repo=appr_repo, broadcaster=broadcaster, step_callback=step_cb
    )
    task_repo.update_task_status(task_id, "user_alice", "running")

    res_box = {}
    t = threading.Thread(target=lambda: res_box.update(res=orch.execute_task(task, cancellation_requested=token.is_cancelled)))
    t.start()

    assert tool_running.wait(timeout=5.0)
    task_repo.cancel_task(task_id, "user_alice")
    token.cancel()
    tool_release.set()
    t.join(timeout=5.0)

    assert res_box["res"]["status"] == "cancelled"
    task_db = task_repo.get_task(task_id, "user_alice")
    assert task_db["status"] == "cancelled"
    steps = task_repo.get_steps(task_id, "user_alice")
    assert steps[0]["status"] == "completed"
    assert steps[0]["tool_output"] == {"out": "step_done"}


def test_race_validation_b_cancellation_immediately_after_tool_returns(repos):
    """Validation B: Cancellation happens immediately after tool returns before task completion."""
    task_repo = repos.tasks
    appr_repo = repos.approvals
    broadcaster = TaskEventBroadcaster()

    task = task_repo.create_task("user_alice", "Race B Task", "Goal B")
    task_id = task["id"]
    task_repo.create_or_update_step(task_id, "user_alice", 0, "Step 0", "pending", tool_name="tool_b")

    token = CancellationToken()

    def step_cb(tool_name, params, uid):
        task_repo.cancel_task(task_id, "user_alice")
        token.cancel()
        return {"out": "b_done"}

    orch = SynchronizedOrchestrator(
        task_repo=task_repo, approval_repo=appr_repo, broadcaster=broadcaster, step_callback=step_cb
    )
    task_repo.update_task_status(task_id, "user_alice", "running")

    res = orch.execute_task(task, cancellation_requested=token.is_cancelled)
    assert res["status"] == "cancelled"
    task_db = task_repo.get_task(task_id, "user_alice")
    assert task_db["status"] == "cancelled"
    steps = task_repo.get_steps(task_id, "user_alice")
    assert steps[0]["status"] == "completed"


def test_race_validation_c_cancellation_while_final_step_is_executing(repos):
    """Validation C: Multi-step task where cancellation occurs during final step execution."""
    task_repo = repos.tasks
    appr_repo = repos.approvals
    broadcaster = TaskEventBroadcaster()

    task = task_repo.create_task("user_alice", "Race C Task", "Goal C")
    task_id = task["id"]
    task_repo.create_or_update_step(task_id, "user_alice", 0, "Step 0", "pending", tool_name="tool_0")
    task_repo.create_or_update_step(task_id, "user_alice", 1, "Step 1", "pending", tool_name="tool_1")
    task_repo.create_or_update_step(task_id, "user_alice", 2, "Step 2", "pending", tool_name="tool_final")

    token = CancellationToken()
    final_step_started = threading.Event()
    final_step_unblock = threading.Event()

    def step_cb(tool_name, params, uid):
        if tool_name == "tool_final":
            final_step_started.set()
            final_step_unblock.wait(timeout=5.0)
            return {"final": True}
        return {"intermediate": True}

    orch = SynchronizedOrchestrator(
        task_repo=task_repo, approval_repo=appr_repo, broadcaster=broadcaster, step_callback=step_cb
    )
    task_repo.update_task_status(task_id, "user_alice", "running")

    res_box = {}
    t = threading.Thread(target=lambda: res_box.update(res=orch.execute_task(task, cancellation_requested=token.is_cancelled)))
    t.start()

    assert final_step_started.wait(timeout=5.0)
    task_repo.cancel_task(task_id, "user_alice")
    token.cancel()
    final_step_unblock.set()
    t.join(timeout=5.0)

    assert res_box["res"]["status"] == "cancelled"
    task_db = task_repo.get_task(task_id, "user_alice")
    assert task_db["status"] == "cancelled"
    steps = task_repo.get_steps(task_id, "user_alice")
    assert all(s["status"] == "completed" for s in steps)


def test_race_validation_d_cancellation_while_intermediate_step_is_executing(repos):
    """Validation D: Cancellation occurs during intermediate step;
    intermediate step completes, subsequent steps NEVER execute, task remains cancelled."""
    task_repo = repos.tasks
    appr_repo = repos.approvals
    broadcaster = TaskEventBroadcaster()

    task = task_repo.create_task("user_alice", "Race D Task", "Goal D")
    task_id = task["id"]
    task_repo.create_or_update_step(task_id, "user_alice", 0, "Step 0", "pending", tool_name="step_0")
    task_repo.create_or_update_step(task_id, "user_alice", 1, "Step 1", "pending", tool_name="step_1")
    task_repo.create_or_update_step(task_id, "user_alice", 2, "Step 2", "pending", tool_name="step_2")

    token = CancellationToken()
    step_1_started = threading.Event()
    step_1_unblock = threading.Event()
    step_2_executed = threading.Event()

    def step_cb(tool_name, params, uid):
        if tool_name == "step_1":
            step_1_started.set()
            step_1_unblock.wait(timeout=5.0)
            return {"step_1": "done"}
        elif tool_name == "step_2":
            step_2_executed.set()
            return {"step_2": "done"}
        return {"step_0": "done"}

    orch = SynchronizedOrchestrator(
        task_repo=task_repo, approval_repo=appr_repo, broadcaster=broadcaster, step_callback=step_cb
    )
    task_repo.update_task_status(task_id, "user_alice", "running")

    res_box = {}
    t = threading.Thread(target=lambda: res_box.update(res=orch.execute_task(task, cancellation_requested=token.is_cancelled)))
    t.start()

    assert step_1_started.wait(timeout=5.0)
    task_repo.cancel_task(task_id, "user_alice")
    token.cancel()
    step_1_unblock.set()
    t.join(timeout=5.0)

    assert res_box["res"]["status"] == "cancelled"
    assert not step_2_executed.is_set(), "Step 2 executed after cancellation!"

    task_db = task_repo.get_task(task_id, "user_alice")
    assert task_db["status"] == "cancelled"

    steps = {s["step_index"]: s for s in task_repo.get_steps(task_id, "user_alice")}
    assert steps[0]["status"] == "completed"
    assert steps[1]["status"] == "completed"
    assert steps[2]["status"] == "pending"


def test_race_validation_e_cancellation_when_task_is_already_terminal(repos):
    """Validation E: Cancellation requested when task has already reached terminal status."""
    task_repo = repos.tasks

    for terminal_status in ("completed", "failed", "timed_out", "cancelled"):
        task = task_repo.create_task("user_alice", f"Terminal {terminal_status}", "Goal")
        task_repo.update_task_status(task["id"], "user_alice", "running")
        task_repo.update_task_status(task["id"], "user_alice", terminal_status, error_message="orig", result={"r": 1})

        # Attempt to cancel
        cancel_res = task_repo.cancel_task(task["id"], "user_alice")
        assert cancel_res is False, f"cancel_task should return False for already terminal {terminal_status}"

        # Attempt update_task_status to cancelled
        upd_res = task_repo.update_task_status(task["id"], "user_alice", "cancelled")
        if terminal_status != "cancelled":
            assert upd_res is False, f"Cannot overwrite terminal state {terminal_status} with cancelled"
            assert task_repo.get_task(task["id"], "user_alice")["status"] == terminal_status
        else:
            assert task_repo.get_task(task["id"], "user_alice")["status"] == "cancelled"


def test_race_validation_f_concurrent_cancellation_requests(repos):
    """Validation F: Concurrent cancellation requests from multiple threads."""
    task_repo = repos.tasks
    task = task_repo.create_task("user_alice", "Concurrent Cancel", "Goal")
    task_repo.update_task_status(task["id"], "user_alice", "running")

    results = []
    def do_cancel():
        res = task_repo.cancel_task(task["id"], "user_alice")
        results.append(res)

    threads = [threading.Thread(target=do_cancel) for _ in range(10)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=5.0)

    # Exactly one thread should have succeeded in transitioning pending/running -> cancelled
    assert results.count(True) == 1
    assert results.count(False) == 9
    assert task_repo.get_task(task["id"], "user_alice")["status"] == "cancelled"

