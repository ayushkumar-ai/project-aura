import pytest
from uuid import uuid4
from core.models import AURAResponse
from interfaces.model import ModelInterface
from research.models import SearchItem, WebDocument
from research.providers.fake import FakeWebProvider
from research.service import ResearchService


class MockSynthesisModel(ModelInterface):
    def __init__(self):
        self.recorded_prompts = []

    def generate(self, prompt: str, request_id):
        self.recorded_prompts.append(prompt)
        return AURAResponse(
            request_id=request_id,
            content="Evidence indicates rapid progress [1], though conflicting reports exist [2].",
        )


def test_research_service_end_to_end_m9_3_intelligence():
    search_items = [
        SearchItem(title="Alpha Report", url="https://alpha.com/doc?utm_source=news", snippet="Claim: 100MW output achieved."),
        SearchItem(title="Beta Review", url="https://beta.com/doc", snippet="Claim: 50MW output demonstrated."),
        SearchItem(title="Alpha Report Copy", url="https://alpha.com/doc", snippet="Duplicate link."),
    ]
    docs = {
        "https://alpha.com/doc": WebDocument(
            url="https://alpha.com/doc",
            title="Alpha Report",
            content="The reactor achieved 100MW output in recent commercial operation tests.",
        ),
        "https://beta.com/doc": WebDocument(
            url="https://beta.com/doc",
            title="Beta Review",
            content="The reactor achieved 50MW output according to independent monitoring data.",
        ),
    }

    provider = FakeWebProvider(default_items=search_items, documents_by_url=docs)
    service = ResearchService(
        search_provider=provider,
        fetch_provider=provider,
        max_search_results=5,
        max_fetch_sources=3,
    )

    report = service.research("reactor output")

    # 1. URL deduplication: alpha duplicate removed
    assert len(report.sources) == 2

    # 2. Evidence extraction
    assert len(report.evidence) >= 2
    assert report.evidence[0].source_url in ("https://alpha.com/doc", "https://beta.com/doc")

    # 3. Deterministic contradiction detection
    assert report.has_contradictions is True
    assert any("100MW" in c.claim or "50MW" in c.claim or "mw" in c.claim.lower() for c in report.contradictions)

    # 4. Model synthesis with untrusted boundary and citation formatting
    model = MockSynthesisModel()
    synthesis = service.synthesize_findings(report, model, request_id=uuid4())

    assert len(model.recorded_prompts) == 1
    prompt = model.recorded_prompts[0]
    assert "<untrusted_source_content>" in prompt
    assert "POTENTIAL EVIDENCE CONFLICTS DETECTED" in prompt
    assert "Sources:" in synthesis
    assert "https://alpha.com/doc" in synthesis
    assert "https://beta.com/doc" in synthesis
