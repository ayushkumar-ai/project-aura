import re

from interfaces.knowledge import KnowledgeInterface, KnowledgeRecord


class InMemoryKnowledgeStore(KnowledgeInterface):
    """In-memory knowledge retrieval store for AURA."""

    def __init__(self, records: list[KnowledgeRecord] | None = None):
        self.records = records or []

    def add(self, content: str, source: str = "default") -> None:
        self.records.append(
            KnowledgeRecord(
                content=content,
                source=source,
            )
        )

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[KnowledgeRecord]:
        keywords = {
            match.group(0).lower()
            for match in re.finditer(r"\w+", query)
            if match.group(0).strip()
        }

        if not keywords:
            return []

        results = []

        for record in self.records:
            record_text = record.content.lower()
            if any(keyword in record_text for keyword in keywords):
                results.append(record)

        if top_k is not None and top_k > 0:
            return results[:top_k]

        return results
