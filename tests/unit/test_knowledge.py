from knowledge.in_memory import (
    InMemoryKnowledgeStore,
    KnowledgeRecord,
)


def test_knowledge_records_can_be_loaded():
    store = InMemoryKnowledgeStore()

    store.add(
        content="AURA uses Python for its core implementation.",
        source="aura-docs",
    )

    assert store.records == [
        KnowledgeRecord(
            content="AURA uses Python for its core implementation.",
            source="aura-docs",
        )
    ]


def test_relevant_keyword_query_retrieves_expected_record():
    store = InMemoryKnowledgeStore(
        records=[
            KnowledgeRecord(
                content="AURA uses Python for its core implementation.",
                source="aura-docs",
            ),
            KnowledgeRecord(
                content="AURA has a calculator tool.",
                source="tool-docs",
            ),
        ]
    )

    results = store.retrieve("Python implementation")

    assert len(results) == 1
    assert results[0].content == (
        "AURA uses Python for its core implementation."
    )


def test_irrelevant_records_are_not_selected():
    store = InMemoryKnowledgeStore(
        records=[
            KnowledgeRecord(
                content="AURA uses Python for its core implementation.",
                source="aura-docs",
            ),
            KnowledgeRecord(
                content="The calculator tool performs arithmetic.",
                source="tool-docs",
            ),
        ]
    )

    results = store.retrieve("calculator")

    assert len(results) == 1
    assert results[0].source == "tool-docs"


def test_source_metadata_is_preserved():
    store = InMemoryKnowledgeStore(
        records=[
            KnowledgeRecord(
                content="AURA uses Python.",
                source="architecture.md",
            )
        ]
    )

    results = store.retrieve("Python")

    assert results[0].source == "architecture.md"


def test_no_match_returns_empty_list():
    store = InMemoryKnowledgeStore(
        records=[
            KnowledgeRecord(
                content="AURA uses Python.",
                source="architecture.md",
            )
        ]
    )

    assert store.retrieve("database") == []
