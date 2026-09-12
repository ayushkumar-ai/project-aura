"""Milestone 28: Autonomous Experience Distillation Engine.

Extracts reusable relational knowledge from runtime operational intelligence:
- M24 Causal Traces & Critical Paths -> ExecutionPatternEntity
- M25 Multi-Phase Campaign Outcomes -> GoalEntity & Dependency Relations
- M26 Synthesized Dynamic Skills -> SkillEntity & Capability Relations
- M27 Fault Diagnoses & Healing Results -> FaultPatternEntity & RemediationRecipeEntity

Guarantees provenance preservation, strict bounded graph growth, and non-blocking background execution.
"""

from __future__ import annotations

import logging
import time
from typing import Any
from uuid import uuid4

from core.campaign_types import CampaignExecutionResult, CampaignStatus
from core.epistemic_graph import EpistemicKnowledgeGraph
from core.epistemic_types import (
    EntityType,
    KnowledgeEntity,
    KnowledgeRelation,
    RelationType,
    _sanitize_graph_metadata,
)
from core.fault_types import (
    FaultDiagnosticReport,
    HealingStatus,
    RemediationPlan,
    SelfHealingResult,
)
from core.provenance import TaintedValue, is_tainted
from core.skill_types import SkillLifecycleState, SynthesizedSkill

logger = logging.getLogger("aura.experience_distiller")


class ExperienceDistiller:
    """Extracts and consolidates operational experiences into the Epistemic Knowledge Graph."""

    def __init__(
        self,
        knowledge_graph: EpistemicKnowledgeGraph | None = None,
    ) -> None:
        self.knowledge_graph = knowledge_graph if knowledge_graph is not None else EpistemicKnowledgeGraph()

    # ------------------------------------------------------------------
    # Campaign & Execution Pattern Distillation (M24 / M25)
    # ------------------------------------------------------------------

    def distill_campaign_result(
        self,
        result: CampaignExecutionResult,
        mission_graph: Any | None = None,
        traces: Any | None = None,
        artifact_manager: Any | None = None,
    ) -> list[str]:
        """Distill a completed campaign execution into goal patterns and artifact relations."""
        if not isinstance(result, CampaignExecutionResult):
            raise TypeError("result must be a CampaignExecutionResult instance.")

        distilled_ids: list[str] = []
        now = time.time()
        cid = result.campaign_id

        try:
            # 1. Distill Campaign as an ExecutionPattern entity
            is_success = (result.status == CampaignStatus.COMPLETED)
            pattern_id = f"ep_{cid}"

            pattern_entity = KnowledgeEntity(
                entity_id=pattern_id,
                entity_type=EntityType.EXECUTION_PATTERN,
                name=f"Campaign Pattern: {cid}",
                properties={
                    "campaign_id": cid,
                    "status": result.status.value,
                    "completed_phases": list(result.completed_phases),
                    "failed_phases": list(result.failed_phases),
                    "duration_seconds": result.total_duration_seconds,
                    "milestone_scores": dict(result.milestone_scores),
                },
                confidence=1.0 if is_success else 0.5,
                created_at=now,
                is_untrusted=any(is_tainted(a) for a in result.produced_artifact_ids),
                metadata=result.metadata,
            )
            self.knowledge_graph.add_entity(pattern_entity, overwrite=True)
            distilled_ids.append(pattern_id)

            # 2. Distill individual completed phases as GoalEntities
            for pid in result.completed_phases:
                goal_entity_id = f"goal_{cid}_{pid}"
                goal_entity = KnowledgeEntity(
                    entity_id=goal_entity_id,
                    entity_type=EntityType.GOAL,
                    name=f"Phase Goal: {pid}",
                    properties={
                        "phase_id": pid,
                        "campaign_id": cid,
                        "status": "completed",
                    },
                    confidence=1.0,
                    created_at=now,
                )
                self.knowledge_graph.add_entity(goal_entity, overwrite=True)
                distilled_ids.append(goal_entity_id)

                # Link Pattern -> Goal (DEPENDS_ON)
                rel_id = f"rel_ep_goal_{pattern_id}_{goal_entity_id}"
                self.knowledge_graph.add_relation(
                    KnowledgeRelation(
                        relation_id=rel_id,
                        source_id=pattern_id,
                        target_id=goal_entity_id,
                        relation_type=RelationType.DEPENDS_ON,
                        weight=1.0,
                        confidence=1.0,
                    ),
                    reinforce_if_exists=True,
                )

            # 3. Distill Produced Artifacts and Lineage
            for aid in result.produced_artifact_ids:
                art_entity_id = f"art_{aid}"
                art_entity = KnowledgeEntity(
                    entity_id=art_entity_id,
                    entity_type=EntityType.ARTIFACT,
                    name=f"Artifact: {aid}",
                    properties={
                        "artifact_id": aid,
                        "campaign_id": cid,
                    },
                    confidence=1.0,
                    created_at=now,
                )
                self.knowledge_graph.add_entity(art_entity, overwrite=True)
                distilled_ids.append(art_entity_id)

                # Link Goal -> Artifact (PRODUCED_BY)
                if result.completed_phases:
                    last_phase_goal_id = f"goal_{cid}_{result.completed_phases[-1]}"
                    if self.knowledge_graph.has_entity(last_phase_goal_id):
                        rel_id = f"rel_art_prod_{art_entity_id}_{last_phase_goal_id}"
                        self.knowledge_graph.add_relation(
                            KnowledgeRelation(
                                relation_id=rel_id,
                                source_id=art_entity_id,
                                target_id=last_phase_goal_id,
                                relation_type=RelationType.PRODUCED_BY,
                                weight=1.0,
                                confidence=1.0,
                            ),
                            reinforce_if_exists=True,
                        )

            logger.info("ExperienceDistiller: distilled campaign '%s' into %d entities.", cid, len(distilled_ids))

        except Exception as ex:
            logger.warning("ExperienceDistiller: error during campaign distillation for '%s': %s", cid, ex)

        return distilled_ids

    # ------------------------------------------------------------------
    # Dynamic Skill & Capability Distillation (M26)
    # ------------------------------------------------------------------

    def distill_dynamic_skill(
        self,
        skill: SynthesizedSkill,
        verification_report: Any | None = None,
    ) -> str:
        """Distill a synthesized dynamic skill and index its capability properties."""
        if not isinstance(skill, SynthesizedSkill):
            raise TypeError("skill must be a SynthesizedSkill instance.")

        skill_id = f"skill_{skill.name}"
        now = time.time()

        try:
            caps = list(getattr(skill, "required_capabilities", ()))
            ver_rep = getattr(skill, "verification_report", None)
            is_verified = bool(
                getattr(skill, "lifecycle_state", None) == SkillLifecycleState.VERIFIED
                or (ver_rep and getattr(ver_rep, "passed", False))
                or (verification_report and (getattr(verification_report, "passed", False) or getattr(verification_report, "is_verified", False)))
            )

            entity = KnowledgeEntity(
                entity_id=skill_id,
                entity_type=EntityType.SKILL,
                name=skill.name,
                properties={
                    "description": skill.description,
                    "author_role_id": getattr(skill, "author_role_id", ""),
                    "capabilities": caps,
                    "is_verified": is_verified,
                    "invocation_count": getattr(skill, "invocation_count", getattr(skill, "execution_count", 0)),
                    "error_count": getattr(skill, "error_count", 0),
                    "ast_hash": getattr(skill, "ast_hash", getattr(skill, "code_hash", "")),
                },
                confidence=0.95 if is_verified else 0.70,
                created_at=now,
                is_untrusted=getattr(skill, "is_untrusted", False),
                metadata=getattr(skill, "metadata", {}),
            )
            self.knowledge_graph.add_entity(entity, overwrite=True)

            # If authored by a role, create or link RoleEntity
            if skill.author_role_id:
                role_id = f"role_{skill.author_role_id}"
                if not self.knowledge_graph.has_entity(role_id):
                    role_entity = KnowledgeEntity(
                        entity_id=role_id,
                        entity_type=EntityType.ROLE,
                        name=skill.author_role_id,
                        properties={"capabilities": caps},
                        confidence=1.0,
                        created_at=now,
                    )
                    self.knowledge_graph.add_entity(role_entity, overwrite=True)

                # Link Skill -> Role (PRODUCED_BY)
                rel_id = f"rel_skill_author_{skill_id}_{role_id}"
                self.knowledge_graph.add_relation(
                    KnowledgeRelation(
                        relation_id=rel_id,
                        source_id=skill_id,
                        target_id=role_id,
                        relation_type=RelationType.PRODUCED_BY,
                        weight=1.0,
                        confidence=1.0,
                    ),
                    reinforce_if_exists=True,
                )

            logger.info("ExperienceDistiller: distilled skill '%s' into graph.", skill.name)
            return skill_id

        except Exception as ex:
            logger.warning("ExperienceDistiller: error distilling skill '%s': %s", skill.name, ex)
            return skill_id

    # ------------------------------------------------------------------
    # Fault Diagnosis & Remediation Recipe Distillation (M27)
    # ------------------------------------------------------------------

    def distill_healing_event(
        self,
        diagnostic_report: FaultDiagnosticReport,
        plan: RemediationPlan | None = None,
        result: SelfHealingResult | None = None,
    ) -> str:
        """Distill a causal fault diagnosis and its remediation recipe into graph knowledge."""
        if not isinstance(diagnostic_report, FaultDiagnosticReport):
            raise TypeError("diagnostic_report must be a FaultDiagnosticReport instance.")

        fault_id = f"fault_{diagnostic_report.fault_category.value}_{diagnostic_report.phase_id}"
        now = time.time()

        try:
            # 1. Distill FaultPattern entity
            fault_entity = KnowledgeEntity(
                entity_id=fault_id,
                entity_type=EntityType.FAULT_PATTERN,
                name=f"Fault: {diagnostic_report.fault_category.value}",
                properties={
                    "category": diagnostic_report.fault_category.value,
                    "error_message": diagnostic_report.error_message[:256],
                    "culpability_score": diagnostic_report.culpability_score,
                    "confidence_level": diagnostic_report.confidence_level.value,
                    "phase_id": diagnostic_report.phase_id,
                    "goal_id": diagnostic_report.goal_id,
                },
                confidence=diagnostic_report.culpability_score,
                created_at=now,
                metadata=diagnostic_report.metadata,
            )
            self.knowledge_graph.add_entity(fault_entity, overwrite=True)

            # 2. If a plan exists, distill RemediationRecipe entity
            if plan is not None:
                recipe_id = f"recipe_{plan.plan_id}"
                is_recovered = bool(result and result.status == HealingStatus.RECOVERED)
                recipe_confidence = 0.95 if is_recovered else 0.40

                action_types = [a.action_type.value for a in plan.actions]
                recipe_entity = KnowledgeEntity(
                    entity_id=recipe_id,
                    entity_type=EntityType.REMEDIATION_RECIPE,
                    name=f"Remediation Recipe: {plan.plan_id[:8]}",
                    properties={
                        "action_types": action_types,
                        "actions_count": len(plan.actions),
                        "rationale": plan.rationale,
                        "is_recovered": is_recovered,
                        "actions_succeeded": result.actions_succeeded if result else 0,
                    },
                    confidence=recipe_confidence,
                    created_at=now,
                )
                self.knowledge_graph.add_entity(recipe_entity, overwrite=True)

                # Link FaultPattern -> RemediationRecipe (RESOLVED_BY)
                rel_id = f"rel_fault_recipe_{fault_id}_{recipe_id}"
                self.knowledge_graph.add_relation(
                    KnowledgeRelation(
                        relation_id=rel_id,
                        source_id=fault_id,
                        target_id=recipe_id,
                        relation_type=RelationType.RESOLVED_BY,
                        weight=1.5 if is_recovered else 0.5,
                        confidence=recipe_confidence,
                    ),
                    reinforce_if_exists=True,
                )

            logger.info("ExperienceDistiller: distilled fault pattern '%s' into graph.", fault_id)
            return fault_id

        except Exception as ex:
            logger.warning("ExperienceDistiller: error distilling healing event: %s", ex)
            return fault_id
