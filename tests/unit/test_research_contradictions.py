import pytest
from research.contradictions import detect_contradictions
from research.models import EvidenceConflict, EvidenceItem


def test_detect_contradictions_numerical_mismatch():
    ev1 = EvidenceItem(
        source_url="https://source-a.com/report",
        source_title="Source A",
        source_domain="source-a.com",
        content="The system achieved 1,000 qubits in fault-tolerant operations.",
    )
    ev2 = EvidenceItem(
        source_url="https://source-b.com/news",
        source_title="Source B",
        source_domain="source-b.com",
        content="The laboratory demonstrated 10,000 qubits in fault-tolerant tests.",
    )

    conflicts = detect_contradictions((ev1, ev2), query="qubits")
    assert len(conflicts) >= 1
    assert isinstance(conflicts[0], EvidenceConflict)
    assert conflicts[0].conflict_type == "numerical_mismatch"
    assert "qubits" in conflicts[0].claim


def test_detect_contradictions_polarity_divergence():
    ev1 = EvidenceItem(
        source_url="https://tech-times.com/project",
        source_title="Tech Times",
        source_domain="tech-times.com",
        content="The project has achieved commercial feasibility this quarter.",
    )
    ev2 = EvidenceItem(
        source_url="https://skeptic-review.com/project",
        source_title="Skeptic Review",
        source_domain="skeptic-review.com",
        content="The project failed to achieve commercial feasibility and remains delayed.",
    )

    conflicts = detect_contradictions((ev1, ev2), query="feasibility")
    assert len(conflicts) >= 1
    assert any(c.conflict_type == "polarity_conflict" for c in conflicts)


def test_detect_contradictions_no_conflict_on_same_source():
    ev1 = EvidenceItem(
        source_url="https://source-a.com/report",
        source_title="Source A",
        source_domain="source-a.com",
        content="The system achieved 1,000 qubits in 2025.",
    )
    ev2 = EvidenceItem(
        source_url="https://source-a.com/report",
        source_title="Source A",
        source_domain="source-a.com",
        content="The system upgraded to 5,000 qubits in 2026.",
    )

    conflicts = detect_contradictions((ev1, ev2), query="qubits")
    assert len(conflicts) == 0
