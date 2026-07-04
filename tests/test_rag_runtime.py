from unittest.mock import MagicMock, patch

import pytest

from src.rag.qdrant_client import QdrantDocument, QdrantRuntimeClient
from src.rag.qdrant_smoke import run_smoke
from src.rag.rag_pipeline import RAGPipeline, RAGSnippet
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


def test_qdrant_client_lists_collections():
    response = MagicMock()
    response.json.return_value = {
        "result": {
            "collections": [
                {"name": "security_php"},
                {"name": "security_general"},
            ]
        }
    }

    with patch("src.rag.qdrant_client.httpx.get", return_value=response) as get:
        client = QdrantRuntimeClient(
            url="https://qdrant.example.com",
            api_key="secret",
        )
        collections = client.list_collections()

    assert collections == ("security_php", "security_general")
    assert get.call_args.args[0] == "https://qdrant.example.com/collections"
    assert get.call_args.kwargs["headers"]["api-key"] == "secret"


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


def test_qdrant_client_treats_malformed_confidence_as_lowest():
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
        side_effect=[response_for("not-a-number"), response_for(0.8)],
    ):
        client = QdrantRuntimeClient(url="https://qdrant.example.com")
        documents = client.query_by_filter(
            collections=("security_php", "security_general"),
            filters={"topics": ("owasp_sql_injection",)},
            limit=2,
        )

    assert [doc.content for doc in documents] == [
        "confidence 0.8",
        "confidence not-a-number",
    ]


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


def test_rag_pipeline_logs_query_success(capsys):
    client = MagicMock()
    client.is_configured = True
    client.query_by_filter.return_value = [
        QdrantDocument(
            content="Use prepared statements.",
            metadata={
                "topic": "owasp_sql_injection",
                "category": "security",
                "confidence": 0.92,
            },
            collection="security_php",
        )
    ]
    pipeline = RAGPipeline(client=client, enabled=True)

    pipeline.retrieve(selection())

    output = capsys.readouterr().out
    assert "event=rag_query_started" in output
    assert "event=rag_query_succeeded" in output
    assert "source=github_pr" in output
    assert "language=php" in output
    assert "rag_result_count=1" in output
    assert "latency_ms=" in output


def test_rag_pipeline_handles_malformed_confidence_metadata():
    client = MagicMock()
    client.is_configured = True
    client.query_by_filter.return_value = [
        QdrantDocument(
            content="Use parameterized queries.",
            metadata={
                "topic": "owasp_sql_injection",
                "category": "security",
                "confidence": "not-a-number",
            },
            collection="security_php",
        )
    ]

    pipeline = RAGPipeline(client=client, enabled=True)
    snippets = pipeline.retrieve(selection())

    assert len(snippets) == 1
    assert snippets[0].confidence == 0.0
    assert "confidence: 0.00" in pipeline.format_prompt_snippets(snippets)


def test_rag_pipeline_limits_and_shortens_prompt_snippets():
    pipeline = RAGPipeline(client=MagicMock(), enabled=True, max_results=2)
    long_content = "Use bindings. " * 80
    snippets = [
        RAGSnippet(
            content=long_content,
            topic=f"topic_{index}",
            category="security",
            source_title="",
            source_url="https://example.com",
            confidence=0.9,
            collection="security_php",
        )
        for index in range(3)
    ]

    prompt_block = pipeline.format_prompt_snippets(snippets)

    assert "1. [topic_0]" in prompt_block
    assert "2. [topic_1]" in prompt_block
    assert "3. [topic_2]" not in prompt_block
    assert "..." in prompt_block
    assert "https://example.com" in prompt_block


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
    assert pipeline.last_query_error == "RuntimeError"


def test_rag_pipeline_logs_query_failure(capsys):
    client = MagicMock()
    client.is_configured = True
    client.query_by_filter.side_effect = RuntimeError("qdrant down")
    pipeline = RAGPipeline(client=client, enabled=True)

    assert pipeline.retrieve(selection()) == []

    output = capsys.readouterr().out
    assert "event=rag_query_started" in output
    assert "event=rag_query_failed" in output
    assert "error_type=RuntimeError" in output
    assert "status_code=none" in output
    assert "latency_ms=" in output


def test_rag_pipeline_invalid_env_values_fall_back_to_defaults(monkeypatch, capsys):
    monkeypatch.setenv("RAG_MAX_RESULTS", "0")
    monkeypatch.setenv("RAG_MIN_CONFIDENCE", "not-a-float")

    pipeline = RAGPipeline(client=MagicMock(), enabled=True)

    assert pipeline.max_results == 5
    assert pipeline.min_confidence == 0.65
    output = capsys.readouterr().out
    assert "event=rag_config_invalid name=RAG_MAX_RESULTS" in output
    assert "event=rag_config_invalid name=RAG_MIN_CONFIDENCE" in output


def test_rag_pipeline_uses_topic_mapper_for_context():
    client = MagicMock()
    client.is_configured = True
    client.query_by_filter.return_value = []
    pipeline = RAGPipeline(client=client, enabled=True)
    context = {
        "changed_files": {
            "app/Http/Controllers/UserController.php": (
                "Laravel controller builds raw sql from request input."
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


def test_qdrant_smoke_reports_missing_required_env(monkeypatch):
    for name in ("QDRANT_URL", "QDRANT_API_KEY", "RAG_ENABLED"):
        monkeypatch.delenv(name, raising=False)

    result = run_smoke()

    assert result.ok is False
    assert "QDRANT_URL" in result.message
    assert "QDRANT_API_KEY" in result.message
    assert "RAG_ENABLED" in result.message


def test_qdrant_smoke_requires_rag_enabled_true(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example.com")
    monkeypatch.setenv("QDRANT_API_KEY", "secret")
    monkeypatch.setenv("RAG_ENABLED", "false")

    result = run_smoke()

    assert result.ok is False
    assert "RAG_ENABLED must be true" in result.message


def test_qdrant_smoke_reports_query_failure(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example.com")
    monkeypatch.setenv("QDRANT_API_KEY", "secret")
    monkeypatch.setenv("RAG_ENABLED", "true")

    with patch("src.rag.qdrant_smoke.RAGPipeline") as pipeline_class:
        pipeline = pipeline_class.return_value
        pipeline.topic_mapper.from_context.return_value = selection()
        pipeline.client.list_collections.return_value = ("security_php",)
        pipeline.retrieve.return_value = []
        pipeline.last_query_error = "HTTPStatusError(status_code=401)"

        result = run_smoke()

    assert result.ok is False
    assert "Qdrant query failed" in result.message
    assert "status_code=401" in result.message


def test_qdrant_smoke_reports_empty_cloud_collections(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example.com")
    monkeypatch.setenv("QDRANT_API_KEY", "secret")
    monkeypatch.setenv("RAG_ENABLED", "true")

    with patch("src.rag.qdrant_smoke.RAGPipeline") as pipeline_class:
        pipeline = pipeline_class.return_value
        pipeline.topic_mapper.from_context.return_value = selection()
        pipeline.client.list_collections.return_value = ()

        result = run_smoke()

    assert result.ok is False
    assert "connection succeeded" in result.message
    assert "no collections are indexed yet" in result.message
    assert "security_php" in result.message
    pipeline.retrieve.assert_not_called()


def test_qdrant_smoke_reports_missing_expected_collections(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example.com")
    monkeypatch.setenv("QDRANT_API_KEY", "secret")
    monkeypatch.setenv("RAG_ENABLED", "true")

    with patch("src.rag.qdrant_smoke.RAGPipeline") as pipeline_class:
        pipeline = pipeline_class.return_value
        pipeline.topic_mapper.from_context.return_value = selection()
        pipeline.client.list_collections.return_value = ("bestpractice_js",)

        result = run_smoke()

    assert result.ok is False
    assert "expected RAG collections are missing" in result.message
    assert "security_php" in result.message
    assert "bestpractice_js" in result.message
    pipeline.retrieve.assert_not_called()


def test_qdrant_smoke_runs_read_only_query(monkeypatch):
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.example.com")
    monkeypatch.setenv("QDRANT_API_KEY", "secret")
    monkeypatch.setenv("RAG_ENABLED", "true")

    with patch("src.rag.qdrant_smoke.RAGPipeline") as pipeline_class:
        pipeline = pipeline_class.return_value
        pipeline.topic_mapper.from_context.return_value = selection()
        pipeline.client.list_collections.return_value = ("security_php",)
        pipeline.last_query_error = None
        pipeline.retrieve.return_value = [
            RAGSnippet(
                content="Use prepared statements.",
                topic="owasp_sql_injection",
                category="security",
                source_title="OWASP",
                source_url="https://owasp.org/",
                confidence=0.9,
                collection="security_php",
            )
        ]

        result = run_smoke()

    assert result.ok is True
    assert result.snippet_count == 1
    assert "owasp_sql_injection" in result.message
    pipeline_class.assert_called_once_with(enabled=True)
    pipeline.retrieve.assert_called_once_with(selection())
