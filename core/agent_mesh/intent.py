"""M59 — Intent Classification & Goal Formulation.

Classifies incoming user intents, estimates risk tiers, determines primary mesh roles,
and formulates structured execution goals.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.agent_mesh.types import AgentRole
from core.cognitive_memory.types import scrub_sensitive_content
from core.platform.types import CapabilityRiskLevel


@dataclass
class ClassifiedIntent:
    """Structured understanding of user intent."""
    raw_intent: str
    cleaned_goal: str
    primary_role: AgentRole = AgentRole.GENERALIST
    estimated_risk: CapabilityRiskLevel = CapabilityRiskLevel.LOW
    requires_approval_hint: bool = False
    requires_device_interaction: bool = False
    requires_multimodal: bool = False
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_intent": self.raw_intent,
            "cleaned_goal": self.cleaned_goal,
            "primary_role": self.primary_role.value,
            "estimated_risk": self.estimated_risk.value,
            "requires_approval_hint": self.requires_approval_hint,
            "requires_device_interaction": self.requires_device_interaction,
            "requires_multimodal": self.requires_multimodal,
            "tags": list(self.tags),
        }


class IntentClassifier:
    """Deterministic intent parser and role estimator."""

    _DESTRUCTIVE_KEYWORDS = re.compile(r"(?i)\b(?:delete|destroy|purge|format|wipe|rm\s+-rf|erase|drop\s+table)\b")
    _DEVICE_KEYWORDS = re.compile(r"(?i)\b(?:device|desktop|file|directory|battery|system\s+info|clock|os|filesystem)\b")
    _MULTIMODAL_KEYWORDS = re.compile(r"(?i)\b(?:image|photo|audio|video|picture|screenshot|diagram|pdf|document)\b")
    _RESEARCH_KEYWORDS = re.compile(r"(?i)\b(?:search|find|lookup|research|explain|compare|summarize|history)\b")

    def classify(self, text: str) -> ClassifiedIntent:
        """Classify user query into typed intent and estimated risk envelope."""
        cleaned = scrub_sensitive_content(text).strip()
        if not cleaned:
            return ClassifiedIntent(raw_intent="", cleaned_goal="No-op request", primary_role=AgentRole.GENERALIST)

        risk = CapabilityRiskLevel.LOW
        requires_approval = False
        requires_device = bool(self._DEVICE_KEYWORDS.search(cleaned))
        requires_mm = bool(self._MULTIMODAL_KEYWORDS.search(cleaned))
        is_research = bool(self._RESEARCH_KEYWORDS.search(cleaned))
        is_destructive = bool(self._DESTRUCTIVE_KEYWORDS.search(cleaned))

        tags = []
        if is_destructive:
            risk = CapabilityRiskLevel.HIGH
            requires_approval = True
            tags.append("destructive")

        role = AgentRole.GENERALIST
        if requires_device:
            role = AgentRole.DEVICE
            tags.append("device")
        elif requires_mm:
            role = AgentRole.MULTIMODAL
            tags.append("multimodal")
        elif is_research:
            role = AgentRole.RESEARCH
            tags.append("research")

        return ClassifiedIntent(
            raw_intent=cleaned,
            cleaned_goal=cleaned,
            primary_role=role,
            estimated_risk=risk,
            requires_approval_hint=requires_approval,
            requires_device_interaction=requires_device,
            requires_multimodal=requires_mm,
            tags=tags,
        )
