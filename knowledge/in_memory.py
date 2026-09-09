import re

from interfaces.knowledge import KnowledgeInterface, KnowledgeRecord


class InMemoryKnowledgeStore(KnowledgeInterface):
    """In-memory knowledge retrieval store for AURA."""

    def __init__(self, records: list[KnowledgeRecord] | None = None):
        self.records: list[KnowledgeRecord] = []
        if records:
            for r in records:
                if isinstance(r, KnowledgeRecord):
                    self.records.append(r)

    def add(self, content: str, source: str = "default") -> None:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Knowledge content must be a non-empty string.")
        norm_source = (
            source if isinstance(source, str) and source.strip() else "default"
        )
        self.records.append(
            KnowledgeRecord(
                content=content.strip(),
                source=norm_source,
            )
        )

    def retrieve(
        self,
        query: str,
        top_k: int | None = None,
    ) -> list[KnowledgeRecord]:
        if not isinstance(query, str) or not query.strip():
            return []

        keywords = {
            match.group(0).lower()
            for match in re.finditer(r"\w+", query)
            if match.group(0).strip()
        }

        if not keywords:
            return []

        results = []

        for record in self.records:
            if not isinstance(record, KnowledgeRecord):
                continue
            record_text = getattr(record, "content", "")
            if not isinstance(record_text, str):
                continue
            record_text = record_text.lower()
            if any(keyword in record_text for keyword in keywords):
                results.append(record)

        if top_k is not None and top_k > 0:
            return results[:top_k]

        return results
