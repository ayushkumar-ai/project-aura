import json
import pytest

from core.policy import Policy, PolicyDecision
from core.tool_registry import ToolRegistry
from interfaces.tool_executor import ToolExecutor
from research.models import SearchItem, WebDocument
from research.providers.fake import FakeWebProvider
from research.service import ResearchService
from research.tool import WebSearchTool


@pytest.fixture
def tool_setup():
    provider = FakeWebProvider(
        default_items=[
            SearchItem(title="Doc A", url="https://example.com/a", snippet="Snippet A"),
            SearchItem(title="Doc B", url="https://example.com/b", snippet="Snippet B"),
        ],
        documents_by_url={
            "https://example.com/a": WebDocument(url="https://example.com/a", title="Doc A", content="Content A"),
            "https://example.com/b": WebDocument(url="https://example.com/b", title="Doc B", content="Content B"),
        },
    )
    service = ResearchService(search_provider=provider, fetch_provider=provider)
    tool = WebSearchTool(service=service)

    registry = ToolRegistry()
    registry.register("web_search", tool)

    policy = Policy(authorized_tools={"web_search"})
    tool_executor = ToolExecutor(registry=registry, policy=policy)

    return {
        "tool": tool,
        "registry": registry,
        "policy": policy,
        "tool_executor": tool_executor,
        "service": service,
    }


def test_web_search_tool_direct_execution_string_and_json(tool_setup):
    tool = tool_setup["tool"]

    # String input
    raw_res = tool.execute("machine learning")
    parsed = json.loads(raw_res)
    assert parsed["query"] == "machine learning"
    assert len(parsed["sources"]) == 2
    assert parsed["sources"][0]["title"] == "Doc A"

    # JSON input with max_sources
    json_input = json.dumps({"query": "deep learning", "max_sources": 1})
    raw_res2 = tool.execute(json_input)
    parsed2 = json.loads(raw_res2)
    assert parsed2["query"] == "deep learning"
    assert len(parsed2["sources"]) == 1


def test_web_search_tool_through_tool_executor_and_policy(tool_setup):
    tool_executor = tool_setup["tool_executor"]

    res_str = tool_executor.execute("web_search", "generative ai")
    data = json.loads(res_str)
    assert data["query"] == "generative ai"
    assert len(data["sources"]) == 2


def test_web_search_tool_policy_denial(tool_setup):
    tool_reg = tool_setup["registry"]
    # Policy that does not authorize web_search
    strict_policy = Policy(authorized_tools={"echo"})
    strict_executor = ToolExecutor(registry=tool_reg, policy=strict_policy)

    with pytest.raises(PermissionError, match="Tool 'web_search' is not authorized"):
        strict_executor.execute("web_search", "secret search")
