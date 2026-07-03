import os
from dataclasses import dataclass

from src.rag.qdrant_client import QdrantDocument, QdrantRuntimeClient
from src.rag.topic_mapper import TopicMapper, TopicSelection


@dataclass(frozen=True)
class RAGSnippet:
    content: str
    topic: str
    category: str
    source_title: str
    source_url: str
    confidence: float
    collection: str


class RAGPipeline:
    """Runtime read-only RAG pipeline for curated CodeGuard knowledge."""

    def __init__(
        self,
        client: QdrantRuntimeClient | None = None,
        topic_mapper: TopicMapper | None = None,
        enabled: bool | None = None,
        max_results: int | None = None,
        min_confidence: float | None = None,
    ):
        self.client = client or QdrantRuntimeClient()
        self.topic_mapper = topic_mapper or TopicMapper()
        self.enabled = enabled if enabled is not None else self._env_bool("RAG_ENABLED")
        self.max_results = max_results or int(os.getenv("RAG_MAX_RESULTS", "5"))
        self.min_confidence = (
            min_confidence
            if min_confidence is not None
            else float(os.getenv("RAG_MIN_CONFIDENCE", "0.65"))
        )

    def retrieve_for_context(self, context: dict) -> list[RAGSnippet]:
        selection = self.topic_mapper.from_context(context)
        return self.retrieve(selection)

    def retrieve_for_error(
        self,
        error: dict,
        context: dict | None = None,
    ) -> list[RAGSnippet]:
        selection = self.topic_mapper.from_error(error, context)
        return self.retrieve(selection)

    def retrieve(self, selection: TopicSelection) -> list[RAGSnippet]:
        if not self.enabled or not self.client.is_configured:
            return []

        try:
            documents = self.client.query_by_filter(
                collections=selection.collections,
                filters={
                    "category": selection.category,
                    "language": selection.language,
                    "framework": selection.framework,
                    "topics": selection.topics,
                    "min_confidence": self.min_confidence,
                },
                limit=self.max_results,
            )
        except Exception as exc:
            print(f"[RAG] Qdrant query failed: {type(exc).__name__}")
            return []

        return [self._to_snippet(document) for document in documents][: self.max_results]

    def format_prompt_snippets(self, snippets: list[RAGSnippet]) -> str:
        if not snippets:
            return ""

        lines = ["Relevant curated knowledge:"]
        for index, snippet in enumerate(snippets[: self.max_results], start=1):
            source = snippet.source_title or snippet.source_url or "unknown source"
            lines.append(
                f"{index}. [{snippet.topic}] {snippet.content} "
                f"(source: {source}, confidence: {snippet.confidence:.2f})"
            )
        return "\n".join(lines)

    def _to_snippet(self, document: QdrantDocument) -> RAGSnippet:
        metadata = document.metadata
        return RAGSnippet(
            content=self._shorten(document.content),
            topic=str(metadata.get("topic", "")),
            category=str(metadata.get("category", "")),
            source_title=str(metadata.get("source_title", "")),
            source_url=str(metadata.get("source_url", "")),
            confidence=float(metadata.get("confidence") or 0),
            collection=document.collection,
        )

    def _shorten(self, content: str, max_chars: int = 500) -> str:
        cleaned = " ".join(content.split())
        if len(cleaned) <= max_chars:
            return cleaned
        return f"{cleaned[: max_chars - 3].rstrip()}..."

    def _env_bool(self, name: str) -> bool:
        return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}
