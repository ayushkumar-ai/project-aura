"""M52 — Workflow Orchestrator for Asynchronous Tasks and Approvals.

Coordinates bounded step planning, policy enforcement, human approval halts,
resumption from checkpoints, and ModelGateway-backed execution.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any
from uuid import uuid4

from core.background.event_broadcaster import TaskEventBroadcaster
from core.bounded_planner_executor import (
    BoundedAgenticExecutor,
    BoundedExecutionConfig,
    ExecutionApprovalState,
)
from core.metrics import get_metrics_registry
from core.policy import Policy, PolicyDecision
from core.repositories.base import BaseApprovalRepository, BaseTaskRepository
from core.security_audit import SecurityEventType, get_security_audit_logger
from core.telemetry_context import set_correlation_context
from core.tool_ecosystem import ToolEcosystemRegistry
from interfaces.model import ModelInterface

logger = logging.getLogger("aura.background.workflow_orchestrator")


class WorkflowOrchestrator:
    """Orchestrates persistent asynchronous workflow execution and approval gating."""

    def __init__(
        self,
        task_repo: BaseTaskRepository,
        approval_repo: BaseApprovalRepository,
        broadcaster: TaskEventBroadcaster | None = None,
        model_gateway: ModelInterface | None = None,
        policy_engine: Policy | None = None,
        tool_registry: ToolEcosystemRegistry | None = None,
        executor: BoundedAgenticExecutor | None = None,
    ) -> None:
        self.task_repo = task_repo
        self.approval_repo = approval_repo
        self.broadcaster = broadcaster or TaskEventBroadcaster()
        self.policy = policy_engine or Policy()
        self.tool_registry = tool_registry or ToolEcosystemRegistry(policy_engine=self.policy)
        self.model_gateway = model_gateway
        self.executor = executor or BoundedAgenticExecutor(
            policy_engine=self.policy,
            tool_registry=self.tool_registry,
        )
        self.sensitive_tools = {"web_fetch", "device_action", "file_delete", "system_write", "shell_exec", "eval"}

    def _is_task_cancelled(self, task_id: str, user_id: str, cancellation_requested: Any = None) -> bool:
        """Check whether cooperative cancellation has been requested via token or persisted database record."""
        if cancellation_requested and (callable(cancellation_requested) and cancellation_requested()):
            return True
        try:
            task = self.task_repo.get_task(task_id, user_id)
            if task and task.get("status") == "cancelled":
                return True
        except Exception:
            pass
        return False

    def execute_task(
        self,
        task_dict: dict[str, Any],
        cancellation_requested: Any = None,
    ) -> dict[str, Any]:
        """Execute a leased background task through its step lifecycle."""
        task_id = task_dict["id"]
        user_id = task_dict["user_id"]
        title = task_dict.get("title", "")
        goal = task_dict.get("goal", "")
        timeout_seconds = float(task_dict.get("timeout_seconds", 600))
        start_time = time.time()

        set_correlation_context(user_id=user_id, request_id=task_id)

        logger.info(f"Starting workflow for task '{task_id}' (user: '{user_id}', goal: '{goal[:50]}...')")
        self.broadcaster.publish(task_id, "task_started", {
            "task_id": task_id,
            "title": title,
            "goal": goal,
            "status": "running",
        })

        try:
            metrics = get_metrics_registry()
            metrics.get_gauge("aura_tasks_active").set(1, labels={"status": "running"})
        except Exception:
            pass

        # 1. Check or create steps
        existing_steps = self.task_repo.get_steps(task_id, user_id)
        if not existing_steps:
            # Create bounded plan
            try:
                plan = self.executor.create_plan(goal, user_id=user_id, plan_id=f"plan_{task_id}")
                is_valid, val_err = self.executor.validate_plan(plan)
                if not is_valid:
                    err_msg = f"Plan validation error: {val_err}"
                    self.task_repo.update_task_status(task_id, user_id, "failed", error_message=err_msg)
                    self.broadcaster.publish(task_id, "task_failed", {"task_id": task_id, "error": err_msg})
                    self._record_task_completion("failed", start_time)
                    return {"status": "failed", "error": err_msg}

                skill_tool_map = {
                    "analysis": "analysis_tool",
                    "execution": "execution_tool",
                    "verification": "verification_tool",
                    "generic_execution": "execution_tool",
                }
                for idx, step_node in enumerate(plan.steps):
                    tool_hint = None
                    if step_node.metadata and "tools" in step_node.metadata:
                        th = step_node.metadata["tools"]
                        tool_hint = th[0] if th else None
                    if tool_hint:
                        tool_hint = skill_tool_map.get(tool_hint, tool_hint)
                    assigned_tool = tool_hint or skill_tool_map.get(step_node.skill_name, step_node.skill_name)
                    self.task_repo.create_or_update_step(
                        task_id=task_id,
                        user_id=user_id,
                        step_index=idx,
                        name=step_node.objective,
                        status="pending",
                        tool_name=assigned_tool,
                        tool_input=step_node.input_data,
                    )
                existing_steps = self.task_repo.get_steps(task_id, user_id)
            except Exception as e:
                err_msg = f"Failed to plan task: {e}"
                logger.error(err_msg, exc_info=True)
                self.task_repo.update_task_status(task_id, user_id, "failed", error_message=err_msg)
                self.broadcaster.publish(task_id, "task_failed", {"task_id": task_id, "error": err_msg})
                self._record_task_completion("failed", start_time)
                return {"status": "failed", "error": err_msg}

        # 2. Step execution loop
        step_results: list[dict[str, Any]] = []
        for step in existing_steps:
            step_idx = step["step_index"]
            step_name = step["name"]
            tool_name = step.get("tool_name") or "analysis_tool"

            # Check cooperative cancellation
            if self._is_task_cancelled(task_id, user_id, cancellation_requested):
                logger.info(f"Task '{task_id}' was cancelled by operator")
                self.task_repo.update_task_status(task_id, user_id, "cancelled")
                self.broadcaster.publish(task_id, "task_cancelled", {"task_id": task_id})
                self._record_task_completion("cancelled", start_time)
                return {"status": "cancelled", "task_id": task_id}

            # Check timeout
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                timeout_msg = f"Execution exceeded maximum timeout limit ({timeout_seconds}s)"
                logger.warning(f"Task '{task_id}' timed out: {timeout_msg}")
                self.task_repo.update_task_status(task_id, user_id, "timed_out", error_message=timeout_msg)
                self.broadcaster.publish(task_id, "task_timed_out", {"task_id": task_id, "error": timeout_msg})
                self._record_task_completion("timed_out", start_time)
                return {"status": "timed_out", "error": timeout_msg}

            # Skip already completed steps
            if step["status"] == "completed":
                step_results.append(step.get("tool_output") or {})
                continue

            # Check approval requirement
            is_sensitive = (
                tool_name in self.sensitive_tools
                or "sensitive" in str(step.get("tool_input", "")).lower()
                or "delete" in tool_name.lower()
            )

            # Check if policy denies tool outright
            if self.policy:
                pol_dec = self.policy.authorize_tool(tool_name)
                if pol_dec == PolicyDecision.DENY or pol_dec == "deny":
                    deny_msg = f"Tool '{tool_name}' denied by security policy"
                    logger.warning(f"Policy denied execution on task '{task_id}': {deny_msg}")
                    self.task_repo.create_or_update_step(
                        task_id=task_id, user_id=user_id, step_index=step_idx,
                        name=step_name, status="failed", error_message=deny_msg
                    )
                    self.task_repo.update_task_status(task_id, user_id, "failed", error_message=deny_msg)
                    self.broadcaster.publish(task_id, "task_failed", {"task_id": task_id, "error": deny_msg})
                    get_security_audit_logger().record_event(
                        event_type=SecurityEventType.TOOL_POLICY_VIOLATION,
                        outcome="deny",
                        user_id=user_id,
                        reason=deny_msg,
                        metadata={"task_id": task_id, "tool": tool_name},
                    )
                    self._record_task_completion("failed", start_time)
                    return {"status": "failed", "error": deny_msg}

            if is_sensitive:
                # See if this step has an approval record already decided
                step_appr = self._check_step_approval(task_id, step.get("id"), user_id)
                if step_appr and step_appr.get("status") == "approved":
                    logger.info(f"Step '{step_idx}' for task '{task_id}' was pre-approved; proceeding")
                elif step_appr and step_appr.get("status") == "rejected":
                    reject_msg = f"Step '{step_idx}' rejected by operator: {step_appr.get('decision_reason', '')}"
                    self.task_repo.create_or_update_step(
                        task_id=task_id, user_id=user_id, step_index=step_idx,
                        name=step_name, status="failed", error_message=reject_msg
                    )
                    self.task_repo.update_task_status(task_id, user_id, "failed", error_message=reject_msg)
                    self.broadcaster.publish(task_id, "task_failed", {"task_id": task_id, "error": reject_msg})
                    self._record_task_completion("failed", start_time)
                    return {"status": "failed", "error": reject_msg}
                else:
                    # Halt and create persistent approval
                    if step_appr and step_appr.get("status") == "pending":
                        appr_record = step_appr
                        nonce = appr_record["nonce"]
                    else:
                        nonce = secrets.token_urlsafe(32)
                        appr_record = self.approval_repo.create_approval(
                            task_id=task_id,
                            user_id=user_id,
                            action_type="tool_execution",
                            action_payload={"tool_name": tool_name, "parameters": step.get("tool_input", {})},
                            justification=f"Execution of sensitive action '{tool_name}' on step '{step_name}'",
                            step_id=step.get("id"),
                            risk_level="high",
                            nonce=nonce,
                        )
                    self.task_repo.create_or_update_step(
                        task_id=task_id, user_id=user_id, step_index=step_idx,
                        name=step_name, status="awaiting_approval"
                    )
                    self.task_repo.update_task_status(task_id, user_id, "awaiting_approval")
                    self.broadcaster.publish(task_id, "approval_required", {
                        "task_id": task_id,
                        "approval_id": appr_record["id"],
                        "tool_name": tool_name,
                        "risk_level": appr_record["risk_level"],
                        "justification": appr_record["justification"],
                        "nonce": nonce,
                    })
                    logger.info(f"Task '{task_id}' halted awaiting human approval (approval_id: {appr_record['id']})")
                    try:
                        metrics = get_metrics_registry()
                        metrics.get_gauge("aura_tasks_active").set(1, labels={"status": "awaiting_approval"})
                    except Exception:
                        pass
                    return {"status": "awaiting_approval", "approval_id": appr_record["id"]}

            # Execute step
            self.task_repo.create_or_update_step(
                task_id=task_id, user_id=user_id, step_index=step_idx,
                name=step_name, status="running", tool_name=tool_name
            )
            self.broadcaster.publish(task_id, "step_started", {
                "task_id": task_id, "step_index": step_idx, "name": step_name, "tool": tool_name
            })

            try:
                tool_out = self._execute_step_tool(tool_name, step.get("tool_input") or {}, user_id=user_id)
                self.task_repo.create_or_update_step(
                    task_id=task_id, user_id=user_id, step_index=step_idx,
                    name=step_name, status="completed", tool_name=tool_name,
                    tool_output=tool_out
                )
                self.broadcaster.publish(task_id, "step_completed", {
                    "task_id": task_id, "step_index": step_idx, "name": step_name, "output": tool_out
                })
                step_results.append(tool_out)
                try:
                    get_metrics_registry().get_counter("aura_task_steps_total").inc(labels={"status": "completed"})
                except Exception:
                    pass

                # M52 Hardening Fix #1: Post-tool cooperative cancellation check
                if self._is_task_cancelled(task_id, user_id, cancellation_requested):
                    logger.info(f"Task '{task_id}' was cancelled during or immediately after step '{step_idx}'")
                    self.task_repo.update_task_status(task_id, user_id, "cancelled")
                    self.broadcaster.publish(task_id, "task_cancelled", {"task_id": task_id})
                    self._record_task_completion("cancelled", start_time)
                    return {"status": "cancelled", "task_id": task_id}

            except Exception as se:
                # M52 Hardening: Check cancellation before treating exception as fatal workflow failure
                if self._is_task_cancelled(task_id, user_id, cancellation_requested):
                    logger.info(f"Task '{task_id}' was cancelled while executing step '{step_idx}' which raised: {se}")
                    err_msg = f"Step '{step_idx}' interrupted by cancellation: {se}"
                    self.task_repo.create_or_update_step(
                        task_id=task_id, user_id=user_id, step_index=step_idx,
                        name=step_name, status="failed", error_message=err_msg
                    )
                    self.task_repo.update_task_status(task_id, user_id, "cancelled")
                    self.broadcaster.publish(task_id, "task_cancelled", {"task_id": task_id})
                    self._record_task_completion("cancelled", start_time)
                    return {"status": "cancelled", "task_id": task_id}

                err_msg = f"Step '{step_idx}' ({tool_name}) failed: {se}"
                logger.error(err_msg, exc_info=True)
                self.task_repo.create_or_update_step(
                    task_id=task_id, user_id=user_id, step_index=step_idx,
                    name=step_name, status="failed", error_message=err_msg
                )
                self.task_repo.update_task_status(task_id, user_id, "failed", error_message=err_msg)
                self.broadcaster.publish(task_id, "task_failed", {"task_id": task_id, "error": err_msg})
                self._record_task_completion("failed", start_time)
                return {"status": "failed", "error": err_msg}

        # Check cancellation before marking task completed
        if self._is_task_cancelled(task_id, user_id, cancellation_requested):
            logger.info(f"Task '{task_id}' was cancelled before completion finalization")
            self.task_repo.update_task_status(task_id, user_id, "cancelled")
            self.broadcaster.publish(task_id, "task_cancelled", {"task_id": task_id})
            self._record_task_completion("cancelled", start_time)
            return {"status": "cancelled", "task_id": task_id}

        # 3. Complete task
        final_result = {
            "summary": f"Completed {len(existing_steps)} workflow steps successfully.",
            "steps_count": len(existing_steps),
            "step_outputs": step_results,
        }
        updated = self.task_repo.update_task_status(task_id, user_id, "completed", result=final_result)
        if not updated:
            # M52 Hardening Fix #3: Detect rejected terminal update
            cur_task = self.task_repo.get_task(task_id, user_id)
            cur_status = cur_task.get("status") if cur_task else "unknown"
            logger.warning(
                f"Task '{task_id}' completion update rejected by repository; current durable status is '{cur_status}'"
            )
            # DO NOT publish task_completed! Preserve DB terminal state consistency
            if cur_status == "cancelled":
                self.broadcaster.publish(task_id, "task_cancelled", {"task_id": task_id})
                self._record_task_completion("cancelled", start_time)
                return {"status": "cancelled", "task_id": task_id}
            else:
                self._record_task_completion(cur_status, start_time)
                return {"status": cur_status, "task_id": task_id, "error": f"Task already in terminal state '{cur_status}'"}

        self.broadcaster.publish(task_id, "task_completed", {
            "task_id": task_id,
            "status": "completed",
            "result": final_result,
        })
        self._record_task_completion("completed", start_time)
        logger.info(f"Task '{task_id}' completed successfully in {round(time.time() - start_time, 2)}s")
        return {"status": "completed", "task_id": task_id, "result": final_result}

    def _execute_step_tool(self, tool_name: str, parameters: dict[str, Any], user_id: str) -> dict[str, Any]:
        """Safely execute registered ecosystem tool."""
        eff_tool = tool_name.strip()
        params = dict(parameters or {})
        if eff_tool == "calculator" and "expression" not in params:
            goal_text = str(params.get("goal", ""))
            import re
            m = re.search(r"[\d\s\+\-\*\/\%\(\)\.]+", goal_text)
            params["expression"] = m.group(0).strip() if m else "0"

        if self.tool_registry.get_tool(eff_tool):
            from core.tool_ecosystem_types import ToolExecutionRequest
            req = ToolExecutionRequest(
                tool_name=eff_tool,
                parameters=params,
                caller_role="agent",
                user_id=user_id,
            )
            res = self.tool_registry.execute_tool(req)
            if not res.success:
                raise RuntimeError(f"Tool '{eff_tool}' execution failed: {res.error}")
            return res.to_dict() if hasattr(res, "to_dict") else {"result": str(res.output)}
        elif self.tool_registry.get_tool("execution_tool"):
            from core.tool_ecosystem_types import ToolExecutionRequest
            req = ToolExecutionRequest(
                tool_name="execution_tool",
                parameters={"goal": str(params)},
                caller_role="agent",
                user_id=user_id,
            )
            res = self.tool_registry.execute_tool(req)
            return res.to_dict() if hasattr(res, "to_dict") else {"result": str(res.output)}
        else:
            return {"status": "success", "tool": eff_tool, "output": f"Executed action for: {params}"}

    def _check_step_approval(self, task_id: str, step_id: str | None, user_id: str) -> dict[str, Any] | None:
        """Check if approval exists for this task/step."""
        if hasattr(self.approval_repo, "get_approval_by_task"):
            return self.approval_repo.get_approval_by_task(task_id, user_id, step_id=step_id)
        if hasattr(self.approval_repo, "_approvals"):
            # InMemory fallback
            for a in self.approval_repo._approvals.values():
                if a["user_id"] == user_id and a["task_id"] == task_id:
                    if not step_id or a.get("step_id") == step_id:
                        return a
        return None

    def _record_task_completion(self, terminal_status: str, start_time: float) -> None:
        """Record Prometheus metrics for task lifecycle completion."""
        try:
            duration = max(time.time() - start_time, 0.001)
            metrics = get_metrics_registry()
            metrics.get_gauge("aura_tasks_active").set(0, labels={"status": "running"})
            metrics.get_counter("aura_tasks_total").inc(labels={"status": terminal_status})
            metrics.get_histogram("aura_task_duration_seconds").observe(duration, labels={"status": terminal_status})
        except Exception:
            pass
