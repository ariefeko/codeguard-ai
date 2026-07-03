from unittest.mock import MagicMock, patch

import pytest

from src.rag.qdrant_client import QdrantDocument, QdrantRuntimeClient
from src.rag.rag_pipeline import RAGPipeline
from src.rag.topic_mapper import TopicSelection


def selection() -> TopicSelection:
    return TopicSelection(
        language="php",
        framework="laravel",
        category="security",
        topics=("owasp_sql_injection", "laravel_query_builder_security"),
        collections=("security_php", "security_general"),
        source="github_pr",
    )


def test_qdrant_client_scrolls_collections_with_metadata_filter():
    response = MagicMock()
    response.json.return_value = {
        "result": {
            "points": [
                {
                    "payload": {
                        "content": "Use parameterized queries for user input.",
                        "topic": "owasp_sql_injection",
                        "category": "security",
                        "language": "php",
                        "framework": "laravel",
                        "source_title": "OWASP SQL Injection Prevention",
                        "source_url": "https://owasp.org/",
                        "confidence": 0.91,
                    }
                }
            ]
        }
    }

    with patch("src.rag.qdrant_client.httpx.post", return_value=response) as post:
        client = QdrantRuntimeClient(
            url="https://qdrant.example.com",
            api_key="secret",
        )
        documents = client.query_by_filter(
            collections=("security_php",),
            filters={
                "category": "security",
                "language": "php",
                "framework": "laravel",
                "topics": ("owasp_sql_injection",),
                "min_confidence": 0.65,
            },
            limit=3,
        )

    assert len(documents) == 1
    assert documents[0].content == "Use parameterized queries for user input."
    assert documents[0].collection == "security_php"

    request = post.call_args.kwargs["json"]
    assert request["with_vector"] is False
    assert request["with_payload"] is True
    assert request["limit"] == 3
    assert request["filter"] == {
        "must": [
            {"key": "topic", "match": {"any": ["owasp_sql_injection"]}},
            {"key": "category", "match": {"value": "security"}},
            {"key": "language", "match": {"value": "php"}},
            {"key": "framework", "match": {"value": "laravel"}},
            {"key": "confidence", "range": {"gte": 0.65}},
        ]
    }
    assert post.call_args.kwargs["headers"]["api-key"] == "secret"


def test_qdrant_client_sorts_and_limits_by_confidence():
    def response_for(confidence):
        response = MagicMock()
        response.json.return_value = {
            "result": {
                "points": [
                    {
                        "payload": {
                            "content": f"confidence {confidence}",
                            "topic": "owasp_sql_injection",
                            "confidence": confidence,
                        }
                    }
                ]
            }
        }
        return response

    with patch(
        "src.rag.qdrant_client.httpx.post",
        side_effect=[response_for(0.7), response_for(0.95)],
    ):
        client = QdrantRuntimeClient(url="https://qdrant.example.com")
        documents = client.query_by_filter(
            collections=("security_php", "security_general"),
            filters={"topics": ("owasp_sql_injection",)},
            limit=1,
        )

    assert [doc.content for doc in documents] == ["confidence 0.95"]


def test_qdrant_client_returns_empty_when_not_configured():
    client = QdrantRuntimeClient(url="")

    assert client.query_by_filter(("security_php",), {}, limit=5) == []


def test_rag_pipeline_returns_prompt_ready_snippets():
    client = MagicMock()
    client.is_configured = True
    client.query_by_filter.return_value = [
        QdrantDocument(
            content="Use prepared statements and avoid concatenating raw SQL.",
            metadata={
                "topic": "owasp_sql_injection",
                "category": "security",
                "source_title": "OWASP SQL Injection Prevention",
                "source_url": "https://owasp.org/",
                "confidence": 0.92,
            },
            collection="security_php",
        )
    ]

    pipeline = RAGPipeline(client=client, enabled=True, max_results=5)
    snippets = pipeline.retrieve(selection())

    assert len(snippets) == 1
    assert snippets[0].topic == "owasp_sql_injection"
    assert snippets[0].collection == "security_php"

    prompt_block = pipeline.format_prompt_snippets(snippets)
    assert "Relevant curated knowledge:" in prompt_block
    assert "OWASP SQL Injection Prevention" in prompt_block
    assert "confidence: 0.92" in prompt_block


def test_rag_pipeline_returns_empty_when_disabled():
    client = MagicMock()
    client.is_configured = True
    pipeline = RAGPipeline(client=client, enabled=False)

    assert pipeline.retrieve(selection()) == []
    client.query_by_filter.assert_not_called()


def test_rag_pipeline_falls_back_to_empty_on_qdrant_failure():
    client = MagicMock()
    client.is_configured = True
    client.query_by_filter.side_effect = RuntimeError("qdrant down")
    pipeline = RAGPipeline(client=client, enabled=True)

    assert pipeline.retrieve(selection()) == []


def test_rag_pipeline_uses_topic_mapper_for_context():
    client = MagicMock()
    client.is_configured = True
    client.query_by_filter.return_value = []
    pipeline = RAGPipeline(client=client, enabled=True)
    context = {
        "changed_files": {
            "app/Http/Controllers/UserController.php": (
                "<?php DB::raw('select * from users where id=' . $id);"
            )
        },
        "related_files": {},
    }

    pipeline.retrieve_for_context(context)

    assert client.query_by_filter.call_args.kwargs["collections"] == (
        "security_php",
        "security_general",
    )
    assert "owasp_sql_injection" in client.query_by_filter.call_args.kwargs["filters"]["topics"]


def test_runtime_has_no_write_methods():
    client = QdrantRuntimeClient(url="https://qdrant.example.com")

    for method_name in ("upsert", "delete", "update", "upload", "embed"):
        assert not hasattr(client, method_name)
