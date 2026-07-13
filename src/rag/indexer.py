import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from src.rag.knowledge_base import (
    SEED_PATH,
    load_seed_documents,
    validate_seed_documents,
)


DEFAULT_INDEX_PATH = Path(__file__).parent / "indexed" / "mvp_index.json"
DEFAULT_TTL_DAYS = 30
DEFAULT_VECTOR_SIZE = 1


@dataclass(frozen=True)
class IndexedPoint:
    collection: str
    point_id: str
    vector: tuple[float, ...]
    payload: dict[str, Any]

    def to_qdrant_point(self) -> dict[str, Any]:
        return {
            "id": self.point_id,
            "vector": list(self.vector),
            "payload": self.payload,
        }


@dataclass(frozen=True)
class IndexBundle:
    version: int
    vector_size: int
    generated_at: str
    points: tuple[IndexedPoint, ...]

    def collection_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for point in self.points:
            counts[point.collection] = counts.get(point.collection, 0) + 1
        return counts


def build_index_bundle(
    seed_path: Path | None = None,
    generated_at: str | None = None,
    vector_size: int = DEFAULT_VECTOR_SIZE,
) -> IndexBundle:
    if vector_size < 1:
        raise ValueError("vector_size must be >= 1")

    documents = load_seed_documents(seed_path)
    errors = validate_seed_documents(documents)
    if errors:
        raise ValueError("seed validation failed: " + "; ".join(errors))

    generated = generated_at or date.today().isoformat()
    vector = tuple(0.0 for _ in range(vector_size))
    points = tuple(
        _to_indexed_point(document, generated, vector)
        for document in documents
    )
    return IndexBundle(
        version=1,
        vector_size=vector_size,
        generated_at=generated,
        points=points,
    )


def write_index_bundle(bundle: IndexBundle, path: Path = DEFAULT_INDEX_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(serialize_index_bundle(bundle), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_index_bundle(path: Path = DEFAULT_INDEX_PATH) -> IndexBundle:
    data = json.loads(path.read_text(encoding="utf-8"))
    points = tuple(
        IndexedPoint(
            collection=str(item["collection"]),
            point_id=str(item["point_id"]),
            vector=tuple(float(value) for value in item["vector"]),
            payload=dict(item["payload"]),
        )
        for item in data.get("points", [])
    )
    return IndexBundle(
        version=int(data.get("version", 1)),
        vector_size=int(data.get("vector_size", DEFAULT_VECTOR_SIZE)),
        generated_at=str(data.get("generated_at", "")),
        points=points,
    )


def serialize_index_bundle(bundle: IndexBundle) -> dict[str, Any]:
    return {
        "version": bundle.version,
        "vector_size": bundle.vector_size,
        "generated_at": bundle.generated_at,
        "points": [
            {
                "collection": point.collection,
                "point_id": point.point_id,
                "vector": list(point.vector),
                "payload": point.payload,
            }
            for point in bundle.points
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare the curated RAG seed as a local Qdrant index bundle."
    )
    parser.add_argument("--seed", type=Path, default=SEED_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_INDEX_PATH)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--vector-size", type=int, default=DEFAULT_VECTOR_SIZE)
    args = parser.parse_args()

    bundle = build_index_bundle(args.seed, vector_size=args.vector_size)
    counts = _format_counts(bundle.collection_counts())

    if args.write:
        write_index_bundle(bundle, args.output)
        print(
            "[RAG] "
            "event=rag_index_written "
            f"path={args.output} "
            f"points={len(bundle.points)} "
            f"collections={counts}"
        )
        return 0

    print(
        "[RAG] "
        "event=rag_index_dry_run "
        f"points={len(bundle.points)} "
        f"collections={counts} "
        f"output={args.output}"
    )
    return 0


def _to_indexed_point(
    document: dict[str, Any],
    generated_at: str,
    vector: tuple[float, ...],
) -> IndexedPoint:
    metadata = dict(document["metadata"])
    content = str(document["content"]).strip()
    document_id = str(document["id"])
    collection = str(document["collection"])

    payload = {
        **metadata,
        "id": document_id,
        "document_id": document_id,
        "collection": collection,
        "content": content,
        "content_hash": content_hash(content),
        "framework_version": metadata.get("framework_version", "unknown"),
        "source_type": metadata.get("source_type", "curated_seed"),
        "severity": metadata.get("severity", "unknown"),
        "tags": metadata.get("tags") or _default_tags(metadata),
        "license": metadata.get("license", "public_reference"),
        "last_updated": metadata.get("last_updated", generated_at),
        "ttl_days": metadata.get("ttl_days", DEFAULT_TTL_DAYS),
    }

    return IndexedPoint(
        collection=collection,
        point_id=_point_id(document_id),
        vector=vector,
        payload=payload,
    )


def content_hash(content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _content_hash(content: str) -> str:
    return content_hash(content)


def _point_id(document_id: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"codeguard-rag:{document_id}"))


def _default_tags(metadata: dict[str, Any]) -> list[str]:
    values = [
        str(metadata.get("category", "")),
        str(metadata.get("language", "")),
        str(metadata.get("framework", "")),
        str(metadata.get("topic", "")),
    ]
    return [value for index, value in enumerate(values) if value and value not in values[:index]]


def _format_counts(counts: dict[str, int]) -> str:
    return ",".join(
        f"{collection}:{count}"
        for collection, count in sorted(counts.items())
    )


if __name__ == "__main__":
    raise SystemExit(main())
