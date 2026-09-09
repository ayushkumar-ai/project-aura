import pytest
from research.models import ResearchSource, SearchItem
from research.ranking import (
    rank_research_sources,
    rank_search_items,
    score_research_source,
    score_search_item,
)


def test_score_search_item_keyword_relevance():
    query = "quantum error correction"
    item_relevant = SearchItem(
        title="Quantum Error Correction Breakthrough",
        url="https://example.com/1",
        snippet="Progress in error correction on quantum computers.",
    )
    item_unrelated = SearchItem(
        title="Baking Sourdough Bread",
        url="https://example.com/2",
        snippet="How to bake bread at home.",
    )

    score_rel = score_search_item(item_relevant, query, position_index=0)
    score_unrel = score_search_item(item_unrelated, query, position_index=0)

    assert score_rel > score_unrel


def test_rank_search_items_deterministic_ordering():
    query = "artificial intelligence agents"
    items = [
        SearchItem(title="Cooking AI", url="https://example.com/cook", snippet="Recipes"),
        SearchItem(title="Autonomous Agents in Artificial Intelligence", url="https://example.com/agents", snippet="Overview of AI agents"),
        SearchItem(title="Deep Learning Agents", url="https://example.com/dl", snippet="AI agent systems"),
    ]

    ranked = rank_search_items(items, query)
    assert ranked[0].url == "https://example.com/agents"


def test_score_research_source_content_quality_and_diversity():
    query = "fusion energy"
    src_quality = ResearchSource(
        url="https://science.org/fusion",
        title="Fusion Energy Advances",
        content="Scientists achieved net positive energy in nuclear fusion experiments in 2026.",
        status="success",
        source_domain="science.org",
    )
    src_empty = ResearchSource(
        url="https://blog.com/fusion",
        title="Untitled",
        content="",
        status="success",
        source_domain="blog.com",
    )

    score_q = score_research_source(src_quality, query)
    score_e = score_research_source(src_empty, query)

    assert score_q > score_e


def test_rank_research_sources_attaches_rank_scores():
    query = "space exploration mars"
    sources = [
        ResearchSource(url="https://nasa.gov/mars", title="Mars Exploration", content="NASA explores Mars.", status="success"),
        ResearchSource(url="https://space.com/moon", title="Moon Landing", content="Lunar missions.", status="success"),
    ]
    ranked = rank_research_sources(sources, query)
    assert len(ranked) == 2
    assert ranked[0].rank_score > 0.0
    assert ranked[0].url == "https://nasa.gov/mars"
