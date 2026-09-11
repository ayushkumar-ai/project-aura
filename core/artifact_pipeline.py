"""Cross-Goal Artifact Dataflow Pipelines & Contract Enforcement (M25).

Implements declarative artifact routing channels, precondition contract validation,
schema enforcement, quality threshold gating, and taint security between goals.
"""

from __future__ import annotations

import copy
import logging
import threading
from typing import Any, Sequence

from core.artifact_manager import ArtifactManager
from core.artifact_types import Artifact, ArtifactType, MAX_ARTIFACT_SIZE_BYTES
from core.campaign_types import (
    ArtifactContract,
    DataflowBinding,
    DataflowChannelType,
    MAX_DATAFLOW_BINDINGS,
)
from core.provenance import TaintedValue, is_tainted, wrap_tainted

logger = logging.getLogger("aura.artifact_pipeline")


class ArtifactSchemaValidator:
    """Validates artifact deliverables against consumer ArtifactContract specifications."""

    @staticmethod
    def validate(
        artifact: Artifact,
        content: Any,
        contract: ArtifactContract | None,
    ) -> tuple[bool, str | None]:
        """Validate artifact metadata and content against contract. Returns (is_valid, error_message)."""
        if contract is None:
            return True, None

        # 1. Type validation
        if artifact.artifact_type != contract.expected_artifact_type:
            return False, (
                f"Artifact type mismatch: expected '{contract.expected_artifact_type.value}', "
                f"got '{artifact.artifact_type.value}' for artifact '{artifact.name}'."
            )

        # 2. MIME type validation
        if contract.required_mime_types:
            if artifact.mime_type.lower() not in [m.lower() for m in contract.required_mime_types]:
                return False, (
                    f"MIME type mismatch: '{artifact.mime_type}' not in required list "
                    f"{contract.required_mime_types} for artifact '{artifact.name}'."
                )

        # 3. Size validation
        if artifact.size_bytes > contract.max_size_bytes:
            return False, (
                f"Artifact size ({artifact.size_bytes} bytes) exceeds contract maximum "
                f"({contract.max_size_bytes} bytes) for artifact '{artifact.name}'."
            )

        # 4. Version validation
        if contract.allowed_versions and artifact.version not in contract.allowed_versions:
            return False, (
                f"Artifact version {artifact.version} not in allowed versions "
                f"{contract.allowed_versions} for artifact '{artifact.name}'."
            )

        # 5. Producer Role validation
        if contract.producer_role_id and artifact.creator_role_id:
            if artifact.creator_role_id != contract.producer_role_id:
                return False, (
                    f"Producer role mismatch: expected '{contract.producer_role_id}', "
                    f"got '{artifact.creator_role_id}' for artifact '{artifact.name}'."
                )

        # 6. Taint Security validation
        if not contract.allow_tainted:
            if artifact.taint_status or is_tainted(content):
                return False, (
                    f"Tainted artifact rejected: contract '{contract.contract_id}' forbids tainted "
                    f"inputs for artifact '{artifact.name}'."
                )

        # 7. Quality score validation from metadata
        if contract.min_quality_score > 0.0:
            quality = float(artifact.metadata.get("quality_score", 1.0))
            if quality < contract.min_quality_score:
                return False, (
                    f"Quality score {quality:.2f} below contract minimum {contract.min_quality_score:.2f} "
                    f"for artifact '{artifact.name}'."
                )

        # 8. Schema definition validation for structured datasets/documents
        if contract.schema_definition and isinstance(content, dict):
            required_keys = contract.schema_definition.get("required_keys", [])
            for k in required_keys:
                if k not in content:
                    return False, (
                        f"Schema validation failed: missing required key '{k}' in artifact '{artifact.name}'."
                    )

            expected_types = contract.schema_definition.get("field_types", {})
            type_map = {
                "str": str,
                "string": str,
                "int": int,
                "integer": int,
                "float": (int, float),
                "number": (int, float),
                "bool": bool,
                "boolean": bool,
                "list": (list, tuple),
                "dict": dict,
                "object": dict,
            }
            for field_name, type_name in expected_types.items():
                if field_name in content:
                    val = content[field_name]
                    target_py_type = type_map.get(str(type_name).lower())
                    if target_py_type and not isinstance(val, target_py_type):
                        return False, (
                            f"Schema validation failed: field '{field_name}' must be of type '{type_name}', "
                            f"got '{type(val).__name__}' in artifact '{artifact.name}'."
                        )

        return True, None


class DataflowChannel:
    """Thread-safe point-to-point or broadcast data delivery buffer for artifacts."""

    def __init__(self, binding: DataflowBinding, max_buffer_size: int = 16):
        self.binding = binding
        self.max_buffer_size = max_buffer_size
        self._lock = threading.RLock()
        self._buffer: list[str] = []  # list of artifact_ids

    def push(self, artifact_id: str) -> bool:
        """Push an artifact_id into the channel."""
        with self._lock:
            if len(self._buffer) >= self.max_buffer_size:
                logger.warning("Channel '%s' buffer full; dropping oldest entry", self.binding.binding_id)
                self._buffer.pop(0)
            self._buffer.append(artifact_id)
            return True

    def peek_latest(self) -> str | None:
        """Inspect the most recent artifact_id without consuming it."""
        with self._lock:
            return self._buffer[-1] if self._buffer else None

    def read_all(self) -> list[str]:
        """Read all buffered artifact IDs without consuming."""
        with self._lock:
            return list(self._buffer)

    def clear(self) -> None:
        """Flush the channel buffer."""
        with self._lock:
            self._buffer.clear()


class ArtifactPipelineRouter:
    """Routes and validates versioned CAS deliverables across campaign goals."""

    def __init__(self, bindings: Sequence[DataflowBinding] = ()):
        self._lock = threading.RLock()
        self._bindings: dict[str, DataflowBinding] = {}
        self._channels: dict[str, DataflowChannel] = {}
        self.validator = ArtifactSchemaValidator()

        for b in bindings:
            self.register_binding(b)

    def register_binding(self, binding: DataflowBinding) -> None:
        """Register a cross-goal dataflow channel binding."""
        if not isinstance(binding, DataflowBinding):
            raise TypeError("binding must be a DataflowBinding instance.")

        with self._lock:
            if len(self._bindings) >= MAX_DATAFLOW_BINDINGS and binding.binding_id not in self._bindings:
                raise ValueError(
                    f"ArtifactPipelineRouter binding limit reached ({MAX_DATAFLOW_BINDINGS})."
                )
            self._bindings[binding.binding_id] = binding
            self._channels[binding.binding_id] = DataflowChannel(binding)

    def get_binding(self, binding_id: str) -> DataflowBinding | None:
        """Retrieve binding by ID."""
        with self._lock:
            return self._bindings.get(binding_id)

    def list_bindings(self) -> list[DataflowBinding]:
        """Return all registered bindings."""
        with self._lock:
            return list(self._bindings.values())

    def route_goal_artifacts(
        self,
        producer_goal_id: str,
        artifact_ids: Sequence[str],
        artifact_manager: ArtifactManager,
    ) -> dict[str, Any]:
        """Route artifacts produced by producer_goal_id to all bound downstream channels."""
        clean_pid = str(producer_goal_id).strip()
        delivery_report: dict[str, Any] = {
            "producer_goal_id": clean_pid,
            "routed_count": 0,
            "deliveries": [],
            "errors": [],
        }

        with self._lock:
            matching_bindings = [
                b for b in self._bindings.values() if b.source_goal_id == clean_pid
            ]
            if not matching_bindings:
                return delivery_report

            for aid in artifact_ids:
                manifest = artifact_manager.get_artifact(aid)
                if manifest is None:
                    continue

                for binding in matching_bindings:
                    if binding.source_artifact_name == manifest.name or binding.source_artifact_name == "*":
                        content = artifact_manager.get_artifact_content(aid) if hasattr(artifact_manager, "get_artifact_content") else getattr(artifact_manager, "read_artifact_content", lambda x: None)(aid)
                        is_valid, err = self.validator.validate(manifest, content, binding.contract)
                        if not is_valid:
                            delivery_report["errors"].append({
                                "binding_id": binding.binding_id,
                                "artifact_id": aid,
                                "error": err,
                            })
                            logger.warning("Pipeline validation failed on binding '%s': %s", binding.binding_id, err)
                            continue

                        channel = self._channels.get(binding.binding_id)
                        if channel:
                            channel.push(aid)
                            delivery_report["routed_count"] += 1
                            delivery_report["deliveries"].append({
                                "binding_id": binding.binding_id,
                                "target_goal_id": binding.target_goal_id,
                                "target_input_key": binding.target_input_key,
                                "artifact_id": aid,
                                "name": manifest.name,
                            })

        return delivery_report

    def get_input_artifacts_for_goal(
        self,
        consumer_goal_id: str,
        artifact_manager: ArtifactManager,
    ) -> dict[str, Any]:
        """Retrieve and validate all incoming artifact inputs bound to a consumer goal."""
        clean_cid = str(consumer_goal_id).strip()
        resolved_inputs: dict[str, Any] = {}

        with self._lock:
            consumer_bindings = [
                b for b in self._bindings.values() if b.target_goal_id == clean_cid
            ]

            for binding in consumer_bindings:
                channel = self._channels.get(binding.binding_id)
                if not channel:
                    continue

                aid = channel.peek_latest()
                if aid is None:
                    if not binding.is_optional:
                        logger.debug("No artifact delivered yet for required binding '%s'", binding.binding_id)
                    continue

                manifest = artifact_manager.get_artifact(aid)
                if manifest is None:
                    continue

                content = artifact_manager.get_artifact_content(aid) if hasattr(artifact_manager, "get_artifact_content") else getattr(artifact_manager, "read_artifact_content", lambda x: None)(aid)
                is_valid, err = self.validator.validate(manifest, content, binding.contract)
                if not is_valid:
                    logger.error("Input artifact '%s' failed contract on binding '%s': %s", aid, binding.binding_id, err)
                    continue

                # Preserve taint status if incoming artifact is tainted
                if manifest.taint_status and not isinstance(content, TaintedValue):
                    content = wrap_tainted(
                        content,
                        is_untrusted=True,
                        source_type="artifact_pipeline",
                        originating_step_id=aid,
                    )

                resolved_inputs[binding.target_input_key] = {
                    "artifact_id": manifest.artifact_id,
                    "name": manifest.name,
                    "artifact_type": manifest.artifact_type.value,
                    "version": manifest.version,
                    "content_hash": manifest.content_hash,
                    "taint_status": manifest.taint_status,
                    "content": content,
                }

        return resolved_inputs

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "bindings": [b.to_dict() for b in self._bindings.values()],
            }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactPipelineRouter:
        if not isinstance(data, dict):
            raise TypeError("data must be a dictionary.")
        bindings = [DataflowBinding.from_dict(b) for b in data.get("bindings", [])]
        return cls(bindings=bindings)
