"""Milestone 27: Causal Fault Analyzer.

Traverses M24 CausalExecutionGraph backward from a known failure point to isolate
the root-cause span, classify the fault category, compute a confidence score,
and emit an immutable FaultDiagnosticReport.

Design principles:
- Read-only: never modifies traces, artifacts, or runtime state.
- Evidence-based: classification requires observable evidence in span data.
- Conservative: defaults to UNKNOWN / INSUFFICIENT when evidence is weak.
- Security: never attempts to act, only to diagnose.
"""

from __future__ import annotations

import logging
import time
import traceback
from typing import Any

from core.fault_types import (
    ConfidenceLevel,
    FaultCategory,
    FaultDiagnosticReport,
    _sanitize_fault_metadata,
)
from core.trace_types import SpanRecord, SpanStatus
from core.trace_exporter import CausalExecutionGraph

logger = logging.getLogger("aura.causal_fault_analyzer")

# ---------------------------------------------------------------------------
# Evidence keyword patterns (lower-cased) for classification heuristics
# ---------------------------------------------------------------------------
_TRANSIENT_PATTERNS = (
    "timeout",
    "timed out",
    "connectionrefused",
    "connection refused",
    "network",
    "retryable",
    "rate limit",
    "503",
    "502",
    "429",
    "temporaryerror",
)

_SKILL_DEFECT_PATTERNS = (
    "dynamic_skill",
    "skill_synthesis",
    "dynamic_registry",
    "skill_verification",
    "sandboxed_tool",
    "sandbox_executor",
    "skilldefect",
    "execute_skill",
    "code_sandbox",
)

_ARTIFACT_SCHEMA_PATTERNS = (
    "artifact_pipeline",
    "schema_validation",
    "contract",
    "mime type",
    "required key",
    "quality score",
    "size_bytes",
    "artifact_type",
    "schema_definition",
    "artifactschema",
    "dataflow",
)

_RESOURCE_STARVATION_PATTERNS = (
    "budget",
    "resource_budget",
    "lock",
    "resource_lock",
    "quota",
    "capacity",
    "max_active",
    "limit reached",
)

_POLICY_BLOCK_PATTERNS = (
    "not authorized",
    "denied by policy",
    "approval rejected",
    "policy",
    "permissionerror",
    "security review denied",
    "unauthorized",
    "access denied",
    "untrusted",
    "taint",
    "security boundary",
)

_SAGA_FAILURE_PATTERNS = (
    "saga",
    "compensat",
    "rollback",
    "compensation_failed",
)

_SEMANTIC_PATTERNS = (
    "milestone",
    "evaluation_engine",
    "evaluation failed",
    "min_evaluation_score",
    "gate",
    "criteria",
    "semantic",
)


def _text_matches(text: str, patterns: tuple[str, ...]) -> bool:
    """Case-insensitive check whether text contains any of the given patterns."""
    low = text.lower()
    return any(p in low for p in patterns)


def _classify_from_text(error_text: str, span_names: list[str]) -> tuple[FaultCategory, float]:
    """Classify fault category from error text and span names. Returns (category, base_confidence)."""
    combined = " ".join([error_text] + span_names).lower()

    # Ordered by specificity — more specific signals take precedence
    if _text_matches(combined, _POLICY_BLOCK_PATTERNS):
        return FaultCategory.POLICY_SECURITY_BLOCK, 0.85

    if _text_matches(combined, _SKILL_DEFECT_PATTERNS):
        return FaultCategory.DYNAMIC_SKILL_DEFECT, 0.80

    if _text_matches(combined, _ARTIFACT_SCHEMA_PATTERNS):
        return FaultCategory.ARTIFACT_SCHEMA_MISMATCH, 0.80

    if _text_matches(combined, _SAGA_FAILURE_PATTERNS):
        return FaultCategory.SAGA_COMPENSATION_FAILURE, 0.75

    if _text_matches(combined, _SEMANTIC_PATTERNS):
        return FaultCategory.SEMANTIC_CRITERIA_UNMET, 0.70

    if _text_matches(combined, _RESOURCE_STARVATION_PATTERNS):
        return FaultCategory.RESOURCE_STARVATION, 0.70

    if _text_matches(combined, _TRANSIENT_PATTERNS):
        return FaultCategory.TRANSIENT_INFRASTRUCTURE, 0.75

    return FaultCategory.UNKNOWN, 0.20


class CausalFaultAnalyzer:
    """Diagnoses execution failures by backward traversal of M24 causal trace DAGs."""

    def __init__(self, tracer: Any | None = None) -> None:
        """
        Args:
            tracer: Optional M24 Tracer instance. Used to look up the current trace
                    context when a CausalExecutionGraph is not directly provided.
        """
        self._tracer = tracer

    def analyze(
        self,
        campaign_id: str,
        phase_id: str,
        goal_id: str,
        error_message: str,
        error_traceback: str = "",
        causal_graph: CausalExecutionGraph | None = None,
        failing_input: str = "",
        affected_artifact_ids: list[str] | None = None,
        context: dict[str, Any] | None = None,
    ) -> FaultDiagnosticReport:
        """Produce an evidence-based FaultDiagnosticReport.

        Args:
            campaign_id: Campaign that experienced the failure.
            phase_id: Phase that failed.
            goal_id: Goal within the phase that triggered the failure.
            error_message: Exception or error string.
            error_traceback: Formatted traceback (optional but improves accuracy).
            causal_graph: M24 CausalExecutionGraph for backward traversal (optional).
            failing_input: Input data at the point of failure (truncated).
            affected_artifact_ids: Artifact IDs produced in the failing phase.
            context: Additional evidence key/value pairs.

        Returns:
            An immutable FaultDiagnosticReport.
        """
        ctx = context or {}
        evidence_items: list[str] = []
        report_id = f"fdr_{hash(campaign_id + phase_id + goal_id + error_message) & 0xFFFFFF:06x}"

        # 1. Collect span names from causal graph for classification signal
        span_names: list[str] = []
        root_cause_span_id: str | None = None
        trace_score_boost: float = 0.0

        if causal_graph is not None and causal_graph.spans:
            root_cause_span_id, span_names, trace_score_boost = self._extract_trace_evidence(
                causal_graph, error_message, evidence_items
            )

        # 2. Classify fault category and base confidence
        category, base_confidence = _classify_from_text(
            error_text=error_message + " " + error_traceback,
            span_names=span_names,
        )

        # 3. Boost confidence if we have a concrete root-cause span
        confidence_score = min(1.0, base_confidence + trace_score_boost)

        # 4. Further evidence from context
        if ctx.get("phase_error"):
            evidence_items.append(f"Phase error reported: {str(ctx['phase_error'])[:256]}")
        if ctx.get("milestone_score") is not None:
            ms = float(ctx["milestone_score"])
            evidence_items.append(f"Milestone evaluation score: {ms:.3f}")
            if ms < 0.5:
                if category == FaultCategory.UNKNOWN:
                    category = FaultCategory.SEMANTIC_CRITERIA_UNMET
                    confidence_score = max(confidence_score, 0.65)
        if ctx.get("artifact_contract_error"):
            evidence_items.append(f"Artifact contract violation: {str(ctx['artifact_contract_error'])[:256]}")
            if category in (FaultCategory.UNKNOWN,):
                category = FaultCategory.ARTIFACT_SCHEMA_MISMATCH
                confidence_score = max(confidence_score, 0.70)

        # 5. Error traceback as additional evidence
        if error_traceback:
            evidence_items.append(f"Traceback available ({len(error_traceback)} chars).")
            tb_category, tb_conf = _classify_from_text(error_traceback, span_names)
            if tb_conf > base_confidence and tb_category != FaultCategory.UNKNOWN:
                category = tb_category
                confidence_score = min(1.0, confidence_score + 0.05)

        confidence_level = ConfidenceLevel.from_score(confidence_score)
        evidence_items = evidence_items[:20]

        logger.info(
            "CausalFaultAnalyzer: campaign=%s phase=%s goal=%s → category=%s confidence=%s (%.2f)",
            campaign_id,
            phase_id,
            goal_id,
            category.value,
            confidence_level.value,
            confidence_score,
        )

        return FaultDiagnosticReport(
            report_id=report_id,
            campaign_id=str(campaign_id).strip(),
            phase_id=str(phase_id).strip(),
            goal_id=str(goal_id).strip(),
            fault_category=category,
            confidence_level=confidence_level,
            culpability_score=confidence_score,
            error_message=str(error_message or "")[:2048],
            error_traceback=str(error_traceback or "")[:8192],
            root_cause_span_id=root_cause_span_id,
            affected_artifact_ids=tuple(str(a) for a in (affected_artifact_ids or [])),
            failing_input=str(failing_input or "")[:1024],
            evidence_items=tuple(evidence_items),
            timestamp=time.time(),
            metadata=_sanitize_fault_metadata(ctx),
        )

    def _extract_trace_evidence(
        self,
        graph: CausalExecutionGraph,
        error_message: str,
        evidence_items: list[str],
    ) -> tuple[str | None, list[str], float]:
        """Backward-traverse graph to find root-cause span. Returns (span_id, span_names, confidence_boost)."""
        span_names: list[str] = [s.name for s in graph.spans]
        root_cause_span_id: str | None = None
        confidence_boost = 0.0

        # Find all spans that have ERROR status or error events
        error_spans: list[SpanRecord] = [
            s for s in graph.spans
            if s.status == SpanStatus.ERROR
            or any("error" in (e.name or "").lower() or "exception" in (e.name or "").lower()
                   for e in s.events)
        ]

        if error_spans:
            evidence_items.append(f"Trace contains {len(error_spans)} error span(s).")
            confidence_boost += 0.10

            # Walk backward via parent pointers to find the earliest error
            earliest_error_span = self._find_earliest_error_span(graph, error_spans)
            if earliest_error_span is not None:
                root_cause_span_id = earliest_error_span.span_id
                evidence_items.append(f"Root-cause span identified: '{earliest_error_span.name}' (id={earliest_error_span.span_id[:12]})")
                confidence_boost += 0.10

                # Extract error event messages from root-cause span
                for ev in earliest_error_span.events:
                    if ev.name and ("error" in ev.name.lower() or "exception" in ev.name.lower()):
                        if ev.attributes:
                            msg = str(ev.attributes.get("message", ev.attributes.get("exception.message", "")))[:256]
                            if msg:
                                evidence_items.append(f"Span event message: {msg}")
        else:
            evidence_items.append(f"Trace contains {len(graph.spans)} span(s), no explicit error status found.")

        return root_cause_span_id, span_names, confidence_boost

    def _find_earliest_error_span(
        self,
        graph: CausalExecutionGraph,
        error_spans: list[SpanRecord],
    ) -> SpanRecord | None:
        """Walk parent chain upward from error spans to find the earliest root-cause error span."""
        if not error_spans:
            return None

        # Build a set of error span IDs for quick lookup
        error_ids = {s.span_id for s in error_spans}

        # For each error span, walk up to find the earliest ancestor that is also an error span
        earliest: SpanRecord | None = None

        for error_span in error_spans:
            # Walk up the parent chain
            current_id: str | None = error_span.span_id
            candidate = error_span

            while current_id is not None:
                parent_span = graph.get_parent(current_id)
                if parent_span is None:
                    break
                if parent_span.span_id in error_ids:
                    candidate = parent_span
                current_id = parent_span.span_id

            # The candidate is the highest-level error ancestor for this error span
            if earliest is None:
                earliest = candidate
            else:
                # Prefer the span with the earliest start time (root cause fires first)
                if candidate.start_time < earliest.start_time:
                    earliest = candidate

        return earliest
