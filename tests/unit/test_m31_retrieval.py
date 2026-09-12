"""Unit tests for M31 Advanced Retrieval and RAG Pipeline Subsystem."""

from core.durable_state_store import DurablePersonalStateStore
from core.personal_state_types import MemoryCategory
from core.retrieval_pipeline import AdvancedRetrievalPipeline
from core.retrieval_types import (
    AuthorityTier,
    RetrievalQuery,
    RetrievalSourceType,
)


def test_retrieval_knowledge_docs():
    pipeline = AdvancedRetrievalPipeline()
    pipeline.add_knowledge_document(
        doc_id="doc_1",
        title="Project AURA Architecture",
        content="Project AURA is a multi-agent personal intelligence system with 40 milestones.",
        tags=["architecture", "aura"],
    )
    pipeline.add_knowledge_document(
        doc_id="doc_2",
        title="Python Packaging",
        content="Standard Python packaging uses pyproject.toml and setuptools build backend.",
        tags=["python", "packaging"],
    )

    query = RetrievalQuery(query_text="AURA architecture milestones")
    candidates = pipeline.retrieve_candidates(query)
    assert len(candidates) >= 1
    assert candidates[0].candidate_id == "doc_1"
    assert candidates[0].score > 0.3


def test_retrieval_multi_source():
    store = DurablePersonalStateStore()
    store.record_memory(
        category=MemoryCategory.SEMANTIC,
        content="User frequently works with Docker containers and Kubernetes clusters.",
        tags=["docker", "devops"],
    )
    store.record_experience(
        task_description="Deploy Kubernetes cluster with Helm",
        plan_summary="Init helm, apply values.yaml, verify pods",
        outcome="success",
        reward_score=0.95,
    )

    pipeline = AdvancedRetrievalPipeline(durable_state_store=store)
    pipeline.add_knowledge_document(
        doc_id="doc_k8s",
        title="Kubernetes Deployment Guide",
        content="Deploying services on Kubernetes requires pods, services, and ingress configurations.",
    )

    query = RetrievalQuery(query_text="Kubernetes deployment pods")
    candidates = pipeline.retrieve_candidates(query)
    assert len(candidates) >= 2

    source_types = {c.source_type for c in candidates}
    assert RetrievalSourceType.KNOWLEDGE_BASE in source_types
    assert (
        RetrievalSourceType.EXPERIENCES in source_types
        or RetrievalSourceType.PERSONAL_MEMORY in source_types
    )


def test_execute_rag_context_assembly():
    pipeline = AdvancedRetrievalPipeline()
    pipeline.add_knowledge_document(
        doc_id="doc_rag_1",
        title="API Security",
        content="Always use Bearer token authentication and scrub secrets before logging.",
    )

    bundle = pipeline.execute_rag(query="Bearer token authentication security")
    assert bundle.query == "Bearer token authentication security"
    assert len(bundle.candidates) >= 1
    assert "API Security" in bundle.assembled_text
    assert bundle.retrieval_latency_ms >= 0


def test_retrieval_metrics_evaluation():
    pipeline = AdvancedRetrievalPipeline()
    pipeline.add_knowledge_document(doc_id="doc_a", title="Alpha", content="Alpha document about quantum physics.")
    pipeline.add_knowledge_document(doc_id="doc_b", title="Beta", content="Beta document about general relativity.")
    pipeline.add_knowledge_document(doc_id="doc_c", title="Gamma", content="Gamma document about machine learning neural networks.")

    test_suite = [
        ("quantum physics", ["doc_a"]),
        ("general relativity spacetime", ["doc_b"]),
        ("neural networks machine learning", ["doc_c"]),
    ]

    metrics = pipeline.evaluate_retrieval(test_suite, k=3)
    assert metrics.precision_at_k >= 0.3
    assert metrics.recall_at_k == 1.0
    assert metrics.mrr == 1.0
