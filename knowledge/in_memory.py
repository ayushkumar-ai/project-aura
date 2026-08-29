from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeRecord:
    content: str
    source: str


class InMemoryKnowledgeStore:
    def __init__(self, records: list[KnowledgeRecord] | None = None):
        self.records = records or []

    def add(self, content: str, source: str) -> None:
        self.records.append(
            KnowledgeRecord(
                content=content,
                source=source,
            )
        )

    def retrieve(self, query: str) -> list[KnowledgeRecord]:
        keywords = {
            word.lower()
            for word in query.split()
            if word.strip()
        }

        if not keywords:
            return []

        results = []

        for record in self.records:
            record_text = record.content.lower()
            if any(keyword in record_text for keyword in keywords):
                results.append(record)

        return results