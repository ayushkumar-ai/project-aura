import pytest
from core.agent_memory import InMemoryAgentMemoryStore
from core.memory_consolidation import MemoryConsolidator
from core.memory_types import (
    EpisodicRecord,
    MemoryEntry,
    MemoryNamespace,
    MemoryTier,
    SemanticFact,
)
from core.provenance import TaintedValue
from core.reflection_types import (
    ConsolidationRecord,
    ConsolidationSourceType,
    ContradictionRecord,
    DistillationResult,
    ResolutionStrategy,
)
from research.models import (
    ClaimEvidence,
    ClaimVerificationStatus,
    VerifiedClaim,
)


def test_resolve_contradiction_confidence():
    store = InMemoryAgentMemoryStore()
    consolidator = MemoryConsolidator(memory_store=store, belief_revision_threshold=0.85)

    fact_old = SemanticFact(subject="server", predicate="status", object_value="active", confidence=0.7)
    fact_new_high = SemanticFact(subject="server", predicate="status", object_value="inactive", confidence=0.95)

    strat, val, rat = consolidator.resolve_contradiction(fact_old, fact_new_high)
    assert strat == ResolutionStrategy.REPLACE_NEWER_CONFIDENT
    assert val == "inactive"

    fact_new_low = SemanticFact(subject="server", predicate="status", object_value="maintenance", confidence=0.5)
    strat2, val2, rat2 = consolidator.resolve_contradiction(fact_old, fact_new_low)
    assert strat2 == ResolutionStrategy.PRESERVE_EXISTING_CONFIDENT
    assert val2 == "active"


def test_resolve_contradiction_dict_merge():
    store = InMemoryAgentMemoryStore()
    consolidator = MemoryConsolidator(memory_store=store)

    fact_old = SemanticFact(subject="user_prefs", predicate="theme", object_value={"mode": "dark", "fontSize": 12}, confidence=0.8)
    fact_new = SemanticFact(subject="user_prefs", predicate="theme", object_value={"fontSize": 14, "lineHeight": 1.5}, confidence=0.8)

    strat, val, rat = consolidator.resolve_contradiction(fact_old, fact_new)
    assert strat == ResolutionStrategy.MERGE_ATTRIBUTES
    assert val["mode"] == "dark"
    assert val["fontSize"] == 14
    assert val["lineHeight"] == 1.5


def test_consolidate_fact_with_store():
    store = InMemoryAgentMemoryStore()
    consolidator = MemoryConsolidator(memory_store=store)

    fact1 = SemanticFact(subject="project", predicate="deadline", object_value="Friday", confidence=0.6)
    saved_fact, contra = consolidator.consolidate_fact(fact1, namespace=MemoryNamespace.DOMAIN_KNOWLEDGE.value)
    assert contra is None
    assert saved_fact.object_value == "Friday"

    # Now consolidate updated fact with higher confidence
    fact2 = SemanticFact(subject="project", predicate="deadline", object_value="Monday", confidence=0.95)
    saved_fact2, contra2 = consolidator.consolidate_fact(fact2, namespace=MemoryNamespace.DOMAIN_KNOWLEDGE.value)
    assert contra2 is not None
    assert contra2.resolution == ResolutionStrategy.REPLACE_NEWER_CONFIDENT
    assert saved_fact2.object_value == "Monday"

    # Check store has the updated value
    stored_entry = store.get_by_key(tier=MemoryTier.SEMANTIC, namespace=MemoryNamespace.DOMAIN_KNOWLEDGE.value, key=fact1.key)
    assert stored_entry is not None
    assert stored_entry.value == "Monday"


def test_consolidate_episodes():
    store = InMemoryAgentMemoryStore()
    consolidator = MemoryConsolidator(memory_store=store)

    ep1 = EpisodicRecord(task_id="t1", task_goal="g1", success=True, executed_skills=("web_search", "summarize"))
    ep2 = EpisodicRecord(task_id="t2", task_goal="g2", success=True, executed_skills=("web_search", "file_edit"))
    ep3 = EpisodicRecord(task_id="t3", task_goal="g3", success=False, executed_skills=("file_edit",))

    record = consolidator.consolidate_episodes([ep1, ep2, ep3])
    assert isinstance(record, ConsolidationRecord)
    assert record.episodes_analyzed == 3
    assert record.facts_created >= 1

    # Check that skill reliability fact was created in store
    entry = store.get_by_key(tier=MemoryTier.SEMANTIC, namespace=MemoryNamespace.SYSTEM_FACTS.value, key="skill_reliability:web_search")
    assert entry is not None
    assert entry.value["success_rate"] == 1.0
    assert entry.value["sample_size"] == 2


class DummyResearchReport:
    def __init__(self, query: str, claims: list[VerifiedClaim]):
        self.report_id = "rep-test-01"
        self.query = query
        self.verified_claims = claims


def test_distill_research_report_with_taint():
    store = InMemoryAgentMemoryStore()
    consolidator = MemoryConsolidator(memory_store=store)

    ev = ClaimEvidence(
        source_url="https://python.org",
        source_title="Python Home",
        passage="Python 3.12 released",
        stance="supports",
        confidence=0.95,
    )
    claim1 = VerifiedClaim(
        claim_id="claim-py12",
        statement="Python 3.12 includes isolated sub-interpreters",
        verification_status=ClaimVerificationStatus.SUPPORTED,
        confidence_score=0.92,
        supporting_evidence=(ev,),
    )
    claim_unsupported = VerifiedClaim(
        claim_id="claim-unsupported",
        statement="Python 4.0 will be released next year",
        verification_status=ClaimVerificationStatus.UNSUPPORTED,
        confidence_score=0.2,
    )

    report = DummyResearchReport("Python features", [claim1, claim_unsupported])
    result = consolidator.distill_research_report(report, query="Python features")

    assert isinstance(result, DistillationResult)
    assert len(result.facts_distilled) == 1
    distilled = result.facts_distilled[0]
    assert distilled.subject == "Python features"
    assert distilled.predicate == "claim-py12"
    assert distilled.is_untrusted is True
    assert "https://python.org" in distilled.source_urls
    assert isinstance(distilled.object_value, TaintedValue)
    assert distilled.object_value.raw_value == "Python 3.12 includes isolated sub-interpreters"
