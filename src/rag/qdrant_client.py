import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class QdrantDocument:
    content: str
    metadata: dict[str, Any]
    collection: str


class QdrantRuntimeClient:
    """Read-only Qdrant runtime client for curated RAG knowledge."""

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        timeout: float = 10,
    ):
        configured_url = url if url is not None else os.getenv("QDRANT_URL", "")
        self.url = configured_url.rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("QDRANT_API_KEY")
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.url)

    def list_collections(self) -> tuple[str, ...]:
        if not self.is_configured:
            return ()

        response = httpx.get(
            f"{self.url}/collections",
            headers=self._headers(),
            timeout=self.timeout,
        )
        response.raise_for_status()
        result = response.json().get("result", {})
        collections = result.get("collections", []) if isinstance(result, dict) else []
        return tuple(
            str(collection.get("name", ""))
            for collection in collections
            if isinstance(collection, dict) and collection.get("name")
        )

    def query_by_filter(
        self,
        collections: tuple[str, ...],
        filters: dict[str, Any],
        limit: int = 5,
    ) -> list[QdrantDocument]:
        if not self.is_configured or limit <= 0:
            return []

        documents: list[QdrantDocument] = []
        per_collection_limit = max(limit, 1)
        for collection in collections:
            documents.extend(
                self._scroll_collection(collection, filters, per_collection_limit)
            )

        documents.sort(
            key=lambda item: self._safe_float(item.metadata.get("confidence")),
            reverse=True,
        )
        return documents[:limit]

    def _scroll_collection(
        self,
        collection: str,
        filters: dict[str, Any],
        limit: int,
    ) -> list[QdrantDocument]:
        response = httpx.post(
            f"{self.url}/collections/{collection}/points/scroll",
            headers=self._headers(),
            json={
                "limit": limit,
                "with_payload": True,
                "with_vector": False,
                "filter": self._build_filter(filters),
            },
            timeout=self.timeout,
        )
        response.raise_for_status()

        points = self._extract_points(response.json())
        return [
            QdrantDocument(
                content=str(point.get("payload", {}).get("content", "")).strip(),
                metadata=dict(point.get("payload", {})),
                collection=collection,
            )
            for point in points
            if str(point.get("payload", {}).get("content", "")).strip()
        ]

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        return headers

    def _build_filter(self, filters: dict[str, Any]) -> dict[str, Any]:
        must: list[dict[str, Any]] = []

        topics = tuple(filters.get("topics") or ())
        if topics:
            must.append({"key": "topic", "match": {"any": list(topics)}})

        for key in ("category", "language", "framework"):
            value = filters.get(key)
            if value and value != "unknown":
                must.append({"key": key, "match": {"value": value}})

        min_confidence = filters.get("min_confidence")
        if min_confidence is not None:
            must.append({"key": "confidence", "range": {"gte": float(min_confidence)}})

        return {"must": must} if must else {}

    def _extract_points(self, response_data: dict[str, Any]) -> list[dict[str, Any]]:
        result = response_data.get("result", {})
        if isinstance(result, dict):
            points = result.get("points", [])
        else:
            points = result

        return points if isinstance(points, list) else []

    def _safe_float(self, value: Any) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0
