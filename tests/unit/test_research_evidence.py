import pytest
from research.evidence import extract_evidence_from_text, extract_source_evidence
from research.models import EvidenceItem, ResearchSource


def test_extract_evidence_from_text_bounds_passages():
    query = "solar panel efficiency"
    text = (
        "Solar panel efficiency reached 35 percent in recent lab tests. "
        "This breakthrough allows smaller rooftop installations. "
        "Meanwhile, coal usage continues to decline in many regions. "
        "New perovskite tandem cells are driving the rapid efficiency gains."
    )
    evidence = extract_evidence_from_text(
        query=query,
        text=text,
        source_url="https://clean-energy.org/solar",
        source_title="Solar Efficiency 2026",
        source_domain="clean-energy.org",
        max_passages=2,
        max_passage_chars=300,
    )

    assert len(evidence) <= 2
    assert all(isinstance(ev, EvidenceItem) for ev in evidence)
    assert evidence[0].source_url == "https://clean-energy.org/solar"
    assert "Solar panel efficiency" in evidence[0].content or "perovskite" in evidence[0].content


def test_extract_source_evidence_handles_failed_sources():
    query = "any query"
    failed_source = ResearchSource(
        url="https://fail.com",
        title="Fail",
        status="failed",
        error="404 Not Found",
    )
    evidence = extract_source_evidence(failed_source, query)
    assert evidence == ()


def test_extract_evidence_preserves_attribution():
    query = "battery storage"
    source = ResearchSource(
        url="https://energy.gov/batteries",
        title="Grid Battery Storage",
        content="Grid scale battery storage grew by 150 percent this year.",
        status="success",
        source_domain="energy.gov",
    )
    evidence = extract_source_evidence(source, query)
    assert len(evidence) >= 1
    assert evidence[0].source_url == "https://energy.gov/batteries"
    assert evidence[0].source_title == "Grid Battery Storage"
    assert evidence[0].source_domain == "energy.gov"
