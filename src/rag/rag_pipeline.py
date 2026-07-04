import os
from dataclasses import dataclass
from time import perf_counter
from typing import Any

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
        self.max_results = max_results or self._env_int("RAG_MAX_RESULTS", 5)
        self.last_query_error: str | None = None
        self.min_confidence = (
            min_confidence
            if min_confidence is not None
            else self._env_float("RAG_MIN_CONFIDENCE", 0.65)
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
        self.last_query_error = None
        if not self.enabled:
            self._log_event("rag_query_skipped", selection, reason="disabled")
            return []

        if not self.client.is_configured:
            self._log_event("rag_query_skipped", selection, reason="not_configured")
            return []

        started_at = perf_counter()
        self._log_event("rag_query_started", selection)
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
            latency_ms = int((perf_counter() - started_at) * 1000)
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            self.last_query_error = self._format_error(exc, status_code)
            self._log_event(
                "rag_query_failed",
                selection,
                error_type=type(exc).__name__,
                status_code=status_code or "none",
                latency_ms=latency_ms,
            )
            return []

        snippets = [self._to_snippet(document) for document in documents][: self.max_results]
        latency_ms = int((perf_counter() - started_at) * 1000)
        self._log_event(
            "rag_query_succeeded",
            selection,
            rag_result_count=len(snippets),
            latency_ms=latency_ms,
        )
        return snippets

    def format_prompt_snippets(self, snippets: list[RAGSnippet]) -> str:
        if not snippets:
            return ""

        lines = ["Relevant curated knowledge:"]
        for index, snippet in enumerate(snippets[: self.max_results], start=1):
            source = snippet.source_title or snippet.source_url or "unknown source"
            lines.append(
                f"{index}. [{snippet.topic}] {self._shorten(snippet.content)} "
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
            confidence=self._safe_float(metadata.get("confidence")),
            collection=document.collection,
        )

    def _shorten(self, content: str, max_chars: int = 500) -> str:
        cleaned = " ".join(content.split())
        if len(cleaned) <= max_chars:
            return cleaned
        return f"{cleaned[: max_chars - 3].rstrip()}..."

    def _env_bool(self, name: str) -> bool:
        return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}

    def _env_int(self, name: str, default: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError:
            print(f"[RAG] event=rag_config_invalid name={name} using_default={default}")
            return default
        if value < 1:
            print(f"[RAG] event=rag_config_invalid name={name} using_default={default}")
            return default
        return value

    def _env_float(self, name: str, default: float) -> float:
        try:
            value = float(os.getenv(name, str(default)))
        except ValueError:
            print(f"[RAG] event=rag_config_invalid name={name} using_default={default}")
            return default
        if value < 0:
            print(f"[RAG] event=rag_config_invalid name={name} using_default={default}")
            return default
        return value

    def _safe_float(self, value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def _format_error(self, exc: Exception, status_code: int | None) -> str:
        if status_code is None:
            return type(exc).__name__
        return f"{type(exc).__name__}(status_code={status_code})"

    def _log_event(
        self,
        event: str,
        selection: TopicSelection,
        **fields: Any,
    ) -> None:
        base_fields = {
            "event": event,
            "source": selection.source,
            "language": selection.language,
            "framework": selection.framework,
            "category": selection.category,
            "topics": ",".join(selection.topics),
            "collections": ",".join(selection.collections),
        }
        base_fields.update(fields)
        payload = " ".join(f"{key}={value}" for key, value in base_fields.items())
        print(f"[RAG] {payload}")
