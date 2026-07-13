from src.rag.indexer import content_hash
from src.rag.updater import (
    RefreshPayload,
    build_update_plan,
    is_expired,
)


class RecordingRefreshProvider:
    def __init__(self, payloads):
        self.payloads = payloads
        self.called = []

    def refresh(self, document):
        document_id = document["id"]
        self.called.append(document_id)
        return self.payloads.get(document_id)


def test_fresh_document_skips_refresh_provider():
    document = make_document(
        document_id="fresh_doc",
        last_updated="2026-07-01",
        ttl_days=30,
    )
    provider = RecordingRefreshProvider(
        {
            "fresh_doc": RefreshPayload(
                content="Changed guidance that should not be requested for fresh docs."
            )
        }
    )

    plan = build_update_plan([document], provider=provider, today="2026-07-05")

    assert provider.called == []
    assert plan.counts() == {"fresh": 1}
    assert plan.requires_reindex is False
    assert "content_hash" not in plan.documents[0]["metadata"]


def test_expired_unchanged_document_updates_timestamp_only():
    content = "Use parameterized SQL bindings for user-controlled values."
    document = make_document(
        document_id="unchanged_doc",
        content=content,
        last_updated="2026-05-01",
        ttl_days=30,
    )
    provider = RecordingRefreshProvider(
        {
            "unchanged_doc": RefreshPayload(
                content="  Use parameterized SQL bindings for user-controlled values.  "
            )
        }
    )

    plan = build_update_plan([document], provider=provider, today="2026-07-05")
    updated = plan.documents[0]

    assert provider.called == ["unchanged_doc"]
    assert plan.counts() == {"timestamp_updated": 1}
    assert plan.requires_reindex is False
    assert updated["content"] == content
    assert updated["metadata"]["last_updated"] == "2026-07-05"
    assert updated["metadata"]["content_hash"] == content_hash(content)


def test_expired_changed_document_prepares_reindex_path():
    old_content = "Use parameterized SQL bindings for user-controlled values."
    new_content = (
        "Use parameterized SQL bindings for user-controlled values, and keep "
        "dynamic table or column names restricted to trusted allow-lists."
    )
    document = make_document(
        document_id="changed_doc",
        content=old_content,
        last_updated="2026-05-01",
        ttl_days=30,
    )
    provider = RecordingRefreshProvider(
        {"changed_doc": RefreshPayload(content=new_content)}
    )

    plan = build_update_plan([document], provider=provider, today="2026-07-05")
    updated = plan.documents[0]

    assert plan.counts() == {"content_changed": 1}
    assert plan.requires_reindex is True
    assert updated["content"] == new_content
    assert updated["metadata"]["last_updated"] == "2026-07-05"
    assert updated["metadata"]["content_hash"] == content_hash(new_content)


def test_bad_refresh_payload_is_rejected_before_storing():
    old_content = "Use parameterized SQL bindings for user-controlled values."
    document = make_document(
        document_id="bad_doc",
        content=old_content,
        last_updated="2026-05-01",
        ttl_days=30,
    )
    provider = RecordingRefreshProvider(
        {"bad_doc": RefreshPayload(content="Too short.")}
    )

    plan = build_update_plan([document], provider=provider, today="2026-07-05")
    updated = plan.documents[0]

    assert plan.counts() == {"refresh_rejected": 1}
    assert plan.requires_reindex is False
    assert updated["content"] == old_content
    assert updated["metadata"]["last_updated"] == "2026-05-01"
    assert "content_hash" not in updated["metadata"]
    assert "content must be >=" in plan.decisions[0].reason


def test_missing_last_updated_counts_as_expired():
    document = make_document(document_id="missing_timestamp")
    document["metadata"].pop("last_updated")

    assert is_expired(document, today="2026-07-05") is True


def make_document(
    document_id="doc",
    content="Use parameterized SQL bindings for user-controlled values.",
    last_updated="2026-05-01",
    ttl_days=30,
):
    return {
        "id": document_id,
        "collection": "security_php",
        "content": content,
        "metadata": {
            "topic": "owasp_sql_injection",
            "category": "security",
            "language": "php",
            "framework": "laravel",
            "source_title": "OWASP SQL Injection Prevention Cheat Sheet",
            "source_url": "https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html",
            "confidence": 0.95,
            "last_updated": last_updated,
            "ttl_days": ttl_days,
        },
    }
