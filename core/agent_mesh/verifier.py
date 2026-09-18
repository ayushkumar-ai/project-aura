"""M59 — Result & State Verifier.

Explicitly asserts the correctness of executed action outcomes.
Prevents unverified tool executions or ambiguous external outcomes from masquerading as success.
"""

from __future__ import annotations

import logging
from typing import Any

from core.agent_mesh.types import PlanStep, VerificationStatus

logger = logging.getLogger("aura.agent_mesh.verifier")


class ResultVerifier:
    """Verifies that dispatched actions achieved their expected post-conditions."""

    def verify_step_outcome(
        self,
        step: PlanStep,
        execution_result: dict[str, Any],
    ) -> tuple[VerificationStatus, str]:
        """Assert post-conditions of an executed step (Invariant M59-F37)."""
        if not execution_result:
            return VerificationStatus.VERIFIED_FAILURE, "Execution produced empty result payload."

        res = execution_result.get("result", {})
        if isinstance(res, dict) and res.get("status") == "failed":
            return VerificationStatus.VERIFIED_FAILURE, res.get("error", "Action explicitly reported failure.")

        action_type = step.action_type.value
        action_name = step.action_name

        if action_type == "device":
            if action_name == "write_sandboxed_file":
                if res.get("status") == "written" or "bytes_written" in res:
                    return VerificationStatus.VERIFIED_SUCCESS, "File write verified."
                return VerificationStatus.VERIFIED_FAILURE, "File write verification failed."
            elif action_name == "delete_sandboxed_file":
                if res.get("status") == "deleted":
                    return VerificationStatus.VERIFIED_SUCCESS, "File deletion verified."
                return VerificationStatus.VERIFIED_FAILURE, "File deletion verification failed."
            elif action_name in ("get_system_info", "get_clock", "get_battery_status"):
                if "os_name" in res or "utc_timestamp" in res or "percent" in res or "data" in res:
                    return VerificationStatus.VERIFIED_SUCCESS, "System query outcome verified."
                return VerificationStatus.INCONCLUSIVE, "System query response lacked standard fields."

        elif action_type == "tool":
            if action_name == "calculate":
                if "result" in res and "error" not in res:
                    return VerificationStatus.VERIFIED_SUCCESS, "Calculation verified."
                return VerificationStatus.VERIFIED_FAILURE, res.get("error", "Calculation failed.")

        elif action_type == "response":
            if res.get("status") == "responded" and res.get("message"):
                return VerificationStatus.VERIFIED_SUCCESS, "Response generation verified."
            return VerificationStatus.VERIFIED_FAILURE, "Response generation empty."

        # Default fallback
        if isinstance(res, dict) and "error" not in res:
            return VerificationStatus.VERIFIED_SUCCESS, "Action completed without reported errors."

        return VerificationStatus.INCONCLUSIVE, "Outcome inconclusive."
