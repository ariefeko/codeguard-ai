from unittest.mock import MagicMock, patch

import pytest

from src.rag.indexer import (
    build_index_bundle,
    load_index_bundle,
    write_index_bundle,
)
from src.rag.sync import QdrantSyncClient, sync_bundle


class FakeSyncClient:
    def __init__(self, collections=()):
        self.is_configured = True
        self.collections = tuple(collections)
        self.list_called = False
        self.created = []
        self.upserted = []

    def list_collections(self):
        self.list_called = True
        return self.collections

    def create_collection(self, collection, vector_size):
        self.created.append((collection, vector_size))

    def upsert_points(self, collection, points):
        self.upserted.append((collection, points))


def test_indexer_builds_seed_bundle_with_required_payload_metadata():
    bundle = build_index_bundle(generated_at="2026-07-05")

    assert bundle.version == 1
    assert bundle.vector_size == 1
    assert len(bundle.points) > 0
    assert "security_php" in bundle.collection_counts()

    point = bundle.points[0]
    assert point.vector == (0.0,)
    assert point.payload["content"]
    assert point.payload["content_hash"].startswith("sha256:")
    assert point.payload["document_id"] == point.payload["id"]
    assert point.payload["source_type"] == "curated_seed"
    assert point.payload["last_updated"] == "2026-07-05"
    assert point.payload["ttl_days"] == 30
    assert point.payload["tags"]


def test_indexer_write_and_load_round_trip(tmp_path):
    path = tmp_path / "index.json"
    bundle = build_index_bundle(generated_at="2026-07-05")

    write_index_bundle(bundle, path)
    loaded = load_index_bundle(path)

    assert loaded.version == bundle.version
    assert loaded.vector_size == bundle.vector_size
    assert loaded.generated_at == bundle.generated_at
    assert loaded.points[0].to_qdrant_point() == bundle.points[0].to_qdrant_point()


def test_sync_dry_run_does_not_touch_target_by_default():
    bundle = build_index_bundle(generated_at="2026-07-05")
    client = FakeSyncClient(collections=("security_php",))

    summary = sync_bundle(bundle, client=client, dry_run=True)

    assert summary.dry_run is True
    assert summary.points_planned == len(bundle.points)
    assert summary.points_upserted == 0
    assert client.list_called is False
    assert client.created == []
    assert client.upserted == []


def test_sync_dry_run_can_check_target_collections():
    bundle = build_index_bundle(generated_at="2026-07-05")
    client = FakeSyncClient(collections=("security_php",))

    summary = sync_bundle(
        bundle,
        client=client,
        dry_run=True,
        check_target=True,
    )

    assert client.list_called is True
    assert "security_php" not in summary.collections_created
    assert summary.points_upserted == 0


def test_sync_execute_creates_missing_collections_and_upserts_points():
    bundle = build_index_bundle(generated_at="2026-07-05")
    client = FakeSyncClient(collections=("security_php",))

    summary = sync_bundle(bundle, client=client, dry_run=False)

    assert client.list_called is True
    assert client.created
    assert all(vector_size == bundle.vector_size for _, vector_size in client.created)
    assert client.upserted
    assert summary.points_upserted == len(bundle.points)
    assert summary.points_planned == len(bundle.points)


def test_sync_execute_requires_configured_client():
    bundle = build_index_bundle(generated_at="2026-07-05")
    client = MagicMock()
    client.is_configured = False

    with pytest.raises(ValueError, match="QDRANT_URL"):
        sync_bundle(bundle, client=client, dry_run=False)


def test_qdrant_sync_client_create_and_upsert_requests():
    response = MagicMock()
    response.raise_for_status.return_value = None
    point = {
        "id": "00000000-0000-0000-0000-000000000001",
        "vector": [0.0],
        "payload": {"content": "Use prepared statements."},
    }

    with patch("src.rag.sync.httpx.put", return_value=response) as put:
        client = QdrantSyncClient(
            url="https://qdrant.example.com",
            api_key="secret",
        )
        client.create_collection("security_php", vector_size=1)
        client.upsert_points("security_php", [point])

    assert put.call_args_list[0].args[0] == "https://qdrant.example.com/collections/security_php"
    assert put.call_args_list[0].kwargs["json"] == {
        "vectors": {
            "size": 1,
            "distance": "Cosine",
        }
    }
    assert put.call_args_list[1].args[0] == "https://qdrant.example.com/collections/security_php/points"
    assert put.call_args_list[1].kwargs["json"] == {"points": [point]}
    assert put.call_args_list[1].kwargs["params"] == {"wait": "true"}
    assert put.call_args_list[1].kwargs["headers"]["api-key"] == "secret"
