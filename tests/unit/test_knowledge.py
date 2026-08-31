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


def test_knowledge_store_retrieves_query_with_punctuation():
    store = InMemoryKnowledgeStore(
        records=[
            KnowledgeRecord(
                content="The database stores user profiles.",
                source="db-docs",
            )
        ]
    )

    results = store.retrieve("Where is the database?")

    assert len(results) == 1
    assert results[0].source == "db-docs"


def test_knowledge_store_handles_punctuation_only_query():
    store = InMemoryKnowledgeStore(
        records=[
            KnowledgeRecord(
                content="AURA uses Python.",
                source="architecture.md",
            )
        ]
    )

    assert store.retrieve("???") == []


def test_in_memory_knowledge_store_respects_top_k():
    store = InMemoryKnowledgeStore(
        records=[
            KnowledgeRecord(content="Python document one", source="1"),
            KnowledgeRecord(content="Python document two", source="2"),
            KnowledgeRecord(content="Python document three", source="3"),
            KnowledgeRecord(content="Python document four", source="4"),
            KnowledgeRecord(content="Python document five", source="5"),
        ]
    )

    results = store.retrieve("Python", top_k=2)

    assert len(results) == 2
    assert [record.source for record in results] == ["1", "2"]
