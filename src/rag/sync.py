import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from src.rag.indexer import (
    IndexBundle,
    build_index_bundle,
    load_index_bundle,
)


DEFAULT_DISTANCE = "Cosine"


@dataclass(frozen=True)
class SyncSummary:
    dry_run: bool
    collections_seen: tuple[str, ...]
    collections_created: tuple[str, ...]
    points_upserted: int
    points_planned: int


class QdrantSyncClient:
    """Write-capable client used only by local sync tooling."""

    def __init__(
        self,
        url: str | None = None,
        api_key: str | None = None,
        timeout: float = 30,
    ):
        configured_url = url if url is not None else os.getenv("QDRANT_URL", "")
        self.url = configured_url.rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("QDRANT_API_KEY")
        self.timeout = timeout

    @property
    def is_configured(self) -> bool:
        return bool(self.url)

    def list_collections(self) -> tuple[str, ...]:
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

    def create_collection(self, collection: str, vector_size: int) -> None:
        response = httpx.put(
            f"{self.url}/collections/{collection}",
            headers=self._headers(),
            json={
                "vectors": {
                    "size": vector_size,
                    "distance": DEFAULT_DISTANCE,
                }
            },
            timeout=self.timeout,
        )
        response.raise_for_status()

    def upsert_points(
        self,
        collection: str,
        points: list[dict[str, Any]],
        wait: bool = True,
    ) -> None:
        response = httpx.put(
            f"{self.url}/collections/{collection}/points",
            headers=self._headers(),
            params={"wait": str(wait).lower()},
            json={"points": points},
            timeout=self.timeout,
        )
        response.raise_for_status()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["api-key"] = self.api_key
        return headers


def sync_bundle(
    bundle: IndexBundle,
    client: QdrantSyncClient,
    dry_run: bool = True,
    check_target: bool = False,
) -> SyncSummary:
    grouped_points = _group_points(bundle)
    should_check_target = client.is_configured and (check_target or not dry_run)
    available = client.list_collections() if should_check_target else ()
    to_create = tuple(
        collection
        for collection in sorted(grouped_points)
        if collection not in available
    )
    planned_points = sum(len(points) for points in grouped_points.values())

    if dry_run:
        return SyncSummary(
            dry_run=True,
            collections_seen=available,
            collections_created=to_create,
            points_upserted=0,
            points_planned=planned_points,
        )

    if not client.is_configured:
        raise ValueError("QDRANT_URL is required for sync --execute")

    for collection in to_create:
        client.create_collection(collection, bundle.vector_size)

    points_upserted = 0
    for collection, points in grouped_points.items():
        client.upsert_points(collection, points)
        points_upserted += len(points)

    return SyncSummary(
        dry_run=False,
        collections_seen=available,
        collections_created=to_create,
        points_upserted=points_upserted,
        points_planned=planned_points,
    )


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="Sync the prepared curated RAG index bundle to Qdrant."
    )
    parser.add_argument("--index-file", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--check-target", action="store_true")
    parser.add_argument("--allow-missing-api-key", action="store_true")
    args = parser.parse_args()

    bundle = (
        load_index_bundle(args.index_file)
        if args.index_file
        else build_index_bundle()
    )
    client = QdrantSyncClient()

    if args.execute:
        errors = _validate_execute_env(require_api_key=not args.allow_missing_api_key)
        if errors:
            print(
                "[RAG] "
                "event=rag_sync_failed "
                f"message=Invalid sync environment: {', '.join(errors)}"
            )
            return 1

    summary = sync_bundle(
        bundle,
        client=client,
        dry_run=not args.execute,
        check_target=args.check_target,
    )
    event = "rag_sync_executed" if args.execute else "rag_sync_dry_run"
    print(
        "[RAG] "
        f"event={event} "
        f"points_planned={summary.points_planned} "
        f"points_upserted={summary.points_upserted} "
        f"collections_seen={_format_tuple(summary.collections_seen)} "
        f"collections_to_create={_format_tuple(summary.collections_created)}"
    )
    return 0


def _group_points(bundle: IndexBundle) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for point in bundle.points:
        grouped.setdefault(point.collection, []).append(point.to_qdrant_point())
    return grouped


def _validate_execute_env(require_api_key: bool) -> list[str]:
    required = ["QDRANT_URL"]
    if require_api_key:
        required.append("QDRANT_API_KEY")
    return [f"{name} is missing" for name in required if not os.getenv(name, "").strip()]


def _format_tuple(values: tuple[str, ...]) -> str:
    return ",".join(values) if values else "none"


if __name__ == "__main__":
    raise SystemExit(main())
