import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4

from core.role_registry import RoleRegistry

logger = logging.getLogger("aura.agent_delegation")


class CyclicDelegationError(ValueError):
    """Raised when a cyclic delegation loop is detected (e.g. Agent A -> B -> A)."""
    pass


class DelegationDepthExceededError(ValueError):
    """Raised when delegation recursion depth exceeds the configured limit."""
    pass


class DelegationPolicyError(PermissionError):
    """Raised when delegation violates role skill bounds or attempts unauthorized privilege escalation."""
    pass


class DelegationStatus(str, Enum):
    """Execution status for a delegated task contract."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    REJECTED = "rejected"


FORBIDDEN_DELEGATION_METADATA_KEYS = frozenset({
    "is_authorized",
    "is_admin",
    "approved",
    "bypass_policy",
    "sudo",
})


@dataclass(frozen=True)
class DelegationContract:
    """Explicit, policy-governed contract for delegating a subtask between specialized agent roles."""

    delegator_role_id: str
    delegatee_role_id: str
    task_description: str
    delegation_id: str = field(default_factory=lambda: str(uuid4()))
    parent_task_id: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    allowed_skills: tuple[str, ...] = field(default_factory=tuple)
    current_depth: int = 1
    max_depth: int = 3
    timeout_seconds: float = 60.0
    is_untrusted: bool = True
    delegation_path: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.delegator_role_id, str) or not self.delegator_role_id.strip():
            raise ValueError("delegator_role_id must be a non-empty string.")
        norm_delegator = self.delegator_role_id.strip().lower()
        object.__setattr__(self, "delegator_role_id", norm_delegator)

        if not isinstance(self.delegatee_role_id, str) or not self.delegatee_role_id.strip():
            raise ValueError("delegatee_role_id must be a non-empty string.")
        norm_delegatee = self.delegatee_role_id.strip().lower()
        object.__setattr__(self, "delegatee_role_id", norm_delegatee)

        if norm_delegator == norm_delegatee:
            raise ValueError(f"Self-delegation forbidden: role '{norm_delegator}' cannot delegate to itself.")

        if not isinstance(self.task_description, str) or not self.task_description.strip():
            raise ValueError("task_description must be a non-empty string.")
        object.__setattr__(self, "task_description", self.task_description.strip())

        # Construct path including delegator
        raw_path = list(self.delegation_path) if self.delegation_path else [norm_delegator]
        if norm_delegator not in raw_path:
            raw_path.insert(0, norm_delegator)

        # Check cycle: delegatee cannot already be in the ancestry path
        if norm_delegatee in raw_path:
            raise CyclicDelegationError(
                f"Cyclic delegation detected: role '{norm_delegatee}' is already present in delegation path {raw_path}."
            )

        # Check recursion depth
        eff_depth = len(raw_path)
        if eff_depth > self.max_depth:
            raise DelegationDepthExceededError(
                f"Delegation depth ({eff_depth}) exceeds maximum limit ({self.max_depth}). Path: {raw_path} -> {norm_delegatee}"
            )

        object.__setattr__(self, "current_depth", eff_depth)
        object.__setattr__(self, "delegation_path", tuple(raw_path))

        # Sanitize metadata
        cleaned_meta: dict[str, str] = {}
        if isinstance(self.metadata, dict):
            for k, v in self.metadata.items():
                k_str = str(k).strip().lower()
                if k_str in FORBIDDEN_DELEGATION_METADATA_KEYS:
                    logger.warning(
                        "Security warning: Filtered forbidden key '%s' from delegation '%s' metadata.",
                        k,
                        self.delegation_id,
                    )
                    continue
                cleaned_meta[str(k).strip()] = str(v)
        object.__setattr__(self, "metadata", cleaned_meta)

    def to_dict(self) -> dict[str, Any]:
        """Convert delegation contract to a dictionary."""
        return {
            "delegation_id": self.delegation_id,
            "parent_task_id": self.parent_task_id,
            "delegator_role_id": self.delegator_role_id,
            "delegatee_role_id": self.delegatee_role_id,
            "task_description": self.task_description,
            "context": dict(self.context),
            "allowed_skills": list(self.allowed_skills),
            "current_depth": self.current_depth,
            "max_depth": self.max_depth,
            "timeout_seconds": self.timeout_seconds,
            "is_untrusted": self.is_untrusted,
            "delegation_path": list(self.delegation_path),
            "metadata": dict(self.metadata),
        }


@dataclass
class DelegationResult:
    """Represents the outcome of a completed or failed task delegation."""

    delegation_id: str
    delegator_role_id: str
    delegatee_role_id: str
    status: DelegationStatus = DelegationStatus.COMPLETED
    output: str = ""
    result_payload: dict[str, Any] = field(default_factory=dict)
    latency_seconds: float = 0.0
    is_untrusted: bool = True
    error: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "delegation_id": self.delegation_id,
            "delegator_role_id": self.delegator_role_id,
            "delegatee_role_id": self.delegatee_role_id,
            "status": self.status.value,
            "output": self.output,
            "result_payload": dict(self.result_payload),
            "latency_seconds": round(self.latency_seconds, 4),
            "is_untrusted": self.is_untrusted,
            "error": self.error,
            "metadata": dict(self.metadata),
        }


class DelegationTree:
    """Thread-safe tracker and policy validator for active and completed task delegations."""

    def __init__(self, max_active_delegations: int = 50):
        self.max_active_delegations = max_active_delegations
        self._lock = threading.RLock()
        self._active_contracts: dict[str, DelegationContract] = {}
        self._results: dict[str, DelegationResult] = {}
        self._tree_edges: dict[str, list[str]] = {}  # parent_id -> list of child delegation_ids

    def register_delegation(
        self,
        contract: DelegationContract,
        role_registry: RoleRegistry | None = None,
    ) -> None:
        """Validate and record an active task delegation."""
        if not isinstance(contract, DelegationContract):
            raise TypeError("contract must be an instance of DelegationContract.")

        with self._lock:
            if len(self._active_contracts) >= self.max_active_delegations:
                raise ValueError(
                    f"Maximum active delegations limit ({self.max_active_delegations}) reached."
                )

            # Role boundary validation if role registry is present
            if role_registry is not None:
                if not role_registry.has_role(contract.delegator_role_id):
                    raise DelegationPolicyError(f"Delegator role '{contract.delegator_role_id}' is not registered.")
                if not role_registry.has_role(contract.delegatee_role_id):
                    raise DelegationPolicyError(f"Delegatee role '{contract.delegatee_role_id}' is not registered.")

            self._active_contracts[contract.delegation_id] = contract
            parent_key = contract.parent_task_id or "root"
            if parent_key not in self._tree_edges:
                self._tree_edges[parent_key] = []
            self._tree_edges[parent_key].append(contract.delegation_id)
            logger.info(
                "Registered delegation %s: %s -> %s (depth %d)",
                contract.delegation_id,
                contract.delegator_role_id,
                contract.delegatee_role_id,
                contract.current_depth,
            )

    def complete_delegation(self, result: DelegationResult) -> None:
        """Mark a delegation as completed and store result."""
        if not isinstance(result, DelegationResult):
            raise TypeError("result must be an instance of DelegationResult.")

        with self._lock:
            if result.delegation_id in self._active_contracts:
                del self._active_contracts[result.delegation_id]
            self._results[result.delegation_id] = result

    def get_contract(self, delegation_id: str) -> DelegationContract | None:
        """Retrieve active contract by ID."""
        with self._lock:
            return self._active_contracts.get(delegation_id)

    def get_result(self, delegation_id: str) -> DelegationResult | None:
        """Retrieve result by delegation ID."""
        with self._lock:
            return self._results.get(delegation_id)

    def list_active(self) -> list[DelegationContract]:
        """List all currently active delegations."""
        with self._lock:
            return list(self._active_contracts.values())

    def clear(self) -> None:
        """Reset the delegation tree."""
        with self._lock:
            self._active_contracts.clear()
            self._results.clear()
            self._tree_edges.clear()
