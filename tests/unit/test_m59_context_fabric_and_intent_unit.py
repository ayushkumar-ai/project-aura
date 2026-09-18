"""M59 — Cognitive Context Fabric and Intent Classification Unit Tests."""

import pytest
from core.agent_mesh.context import AgentContext, ContextFabric
from core.agent_mesh.intent import ClassifiedIntent, IntentClassifier
from core.agent_mesh.types import AgentRole
from core.cognitive_memory.types import (
    CognitiveMemory,
    CognitiveMemoryType,
    LifecycleState,
    ProvenanceType,
    UserCognitiveProfile,
)
from core.platform.types import (
    DeviceRecord,
    DeviceTrustState,
    DeviceType,
    PlatformType,
    CapabilityRiskLevel,
)
from core.repositories.in_memory_cognitive_memory import InMemoryCognitiveMemoryRepository
from core.repositories.in_memory_platform import InMemoryPlatformRepository


class TestContextFabricAndIntentUnit:
    def test_intent_classifier_general_and_research(self):
        # Invariant M59-F07
        classifier = IntentClassifier()

        general = classifier.classify("What is the weather today?")
        assert general.primary_role == AgentRole.GENERALIST
        assert general.estimated_risk == CapabilityRiskLevel.LOW
        assert general.requires_approval_hint is False

        research = classifier.classify("Search and summarize recent papers on LLM agents")
        assert research.primary_role == AgentRole.RESEARCH
        assert "research" in research.tags

    def test_intent_classifier_device_and_multimodal(self):
        # Invariants M59-F08, M59-F09
        classifier = IntentClassifier()

        dev_intent = classifier.classify("List files in the local workspace directory on desktop")
        assert dev_intent.primary_role == AgentRole.DEVICE
        assert dev_intent.requires_device_interaction is True

        mm_intent = classifier.classify("Inspect the attached screenshot picture and extract text")
        assert mm_intent.primary_role == AgentRole.MULTIMODAL
        assert mm_intent.requires_multimodal is True

    def test_intent_classifier_destructive_and_secret_scrubbing(self):
        # Invariants M59-F10, M59-F49
        classifier = IntentClassifier()

        destr = classifier.classify("Purge all tenant data and delete database records with Bearer sk-1234567890abcdef1234567890")
        assert destr.estimated_risk == CapabilityRiskLevel.HIGH
        assert destr.requires_approval_hint is True
        assert "destructive" in destr.tags
        assert "sk-1234567890" not in destr.cleaned_goal
        assert "[REDACTED_SECRET]" in destr.cleaned_goal

    def test_context_fabric_assembly_and_prompt_formatting(self):
        # Invariants M59-F13, M59-F14, M59-F17, M59-F18
        mem_repo = InMemoryCognitiveMemoryRepository()
        plat_repo = InMemoryPlatformRepository()

        # Seed memory
        mem_repo.record_memory(
            tenant_id="tenant_1",
            content="User prefers Python 3.11 with pytest",
            memory_type=CognitiveMemoryType.PREFERENCE,
            provenance_type=ProvenanceType.USER_EXPLICIT,
            confidence=0.99,
        )
        mem_repo.save_profile(UserCognitiveProfile(
            tenant_id="tenant_1",
            preferences={"formatting": "Always provide type annotations"},
        ))

        # Seed device
        plat_repo.save_device(DeviceRecord(
            device_id="dev_workstation",
            tenant_id="tenant_1",
            name="Alice MacBook Pro",
            device_type=DeviceType.DESKTOP,
            platform=PlatformType.MACOS,
            trust_state=DeviceTrustState.VERIFIED,
        ))

        fabric = ContextFabric(memory_repo=mem_repo, platform_repo=plat_repo)
        ctx = fabric.assemble_context(
            tenant_id="tenant_1",
            user_id="user_alice",
            query="Write a script to process data",
            conversation_history=[
                {"role": "user", "content": "Hello AURA"},
                {"role": "assistant", "content": "Hello! How can I help you today?"},
            ],
        )

        assert len(ctx.memories) == 1
        assert len(ctx.preferences) == 1
        assert len(ctx.active_devices) == 1

        prompt_text = ctx.to_prompt_text(max_chars=4000)
        assert "SYSTEM POLICY:" in prompt_text
        assert "User prefers Python 3.11" in prompt_text
        assert "Alice MacBook Pro" in prompt_text
        assert "RECENT CONVERSATION HISTORY:" in prompt_text

    def test_context_fabric_cross_tenant_isolation(self):
        # Invariant M59-F17
        mem_repo = InMemoryCognitiveMemoryRepository()
        plat_repo = InMemoryPlatformRepository()

        # Seed tenant_A
        mem_repo.record_memory(
            tenant_id="tenant_A",
            content="Confidential project Phoenix specs",
            memory_type=CognitiveMemoryType.EPISODIC,
        )
        plat_repo.save_device(DeviceRecord(
            device_id="dev_A",
            tenant_id="tenant_A",
            name="Tenant A Server",
        ))

        # Query tenant_B
        fabric = ContextFabric(memory_repo=mem_repo, platform_repo=plat_repo)
        ctx_b = fabric.assemble_context(tenant_id="tenant_B", user_id="user_B", query="Check specs")

        assert len(ctx_b.memories) == 0
        assert len(ctx_b.active_devices) == 0
        prompt_b = ctx_b.to_prompt_text()
        assert "Confidential project Phoenix" not in prompt_b
        assert "Tenant A Server" not in prompt_b
