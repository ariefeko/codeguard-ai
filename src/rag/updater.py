import argparse
import copy
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol

from src.rag.indexer import DEFAULT_TTL_DAYS, content_hash
from src.rag.knowledge_base import (
    MAX_SEED_CONTENT_CHARS,
    SEED_PATH,
    load_seed_documents,
    validate_seed_documents,
)


MIN_REFRESH_CONTENT_CHARS = 40


@dataclass(frozen=True)
class RefreshPayload:
    content: str
    source_title: str | None = None
    source_url: str | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class UpdateDecision:
    document_id: str
    topic: str
    status: str
    current_hash: str
    refreshed_hash: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class UpdatePlan:
    generated_at: str
    documents: tuple[dict[str, Any], ...]
    decisions: tuple[UpdateDecision, ...]

    @property
    def requires_reindex(self) -> bool:
        return any(decision.status == "content_changed" for decision in self.decisions)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for decision in self.decisions:
            counts[decision.status] = counts.get(decision.status, 0) + 1
        return counts


class RefreshProvider(Protocol):
    def refresh(self, document: dict[str, Any]) -> RefreshPayload | None:
        """Return refreshed content for one expired document, or None if unavailable."""


class NoopRefreshProvider:
    def refresh(self, document: dict[str, Any]) -> RefreshPayload | None:
        return None


class LocalUpdatesProvider:
    def __init__(self, path: Path):
        self.updates = _load_updates_file(path)

    def refresh(self, document: dict[str, Any]) -> RefreshPayload | None:
        document_id = str(document.get("id", ""))
        item = self.updates.get(document_id)
        if item is None:
            return None
        if isinstance(item, str):
            return RefreshPayload(content=item)
        return RefreshPayload(
            content=str(item.get("content", "")),
            source_title=item.get("source_title"),
            source_url=item.get("source_url"),
            confidence=item.get("confidence"),
        )


class TavilyRefreshProvider:
    """Optional local refresher; instantiated only by the updater CLI."""

    def __init__(self):
        from src.orchestration.tavily_client import CodeGuardSearch

        self.search = CodeGuardSearch()

    def refresh(self, document: dict[str, Any]) -> RefreshPayload | None:
        metadata = document.get("metadata", {})
        topic = str(metadata.get("topic", "")).replace("_", " ")
        category = str(metadata.get("category", ""))
        language = str(metadata.get("language", ""))
        framework = str(metadata.get("framework", ""))

        if category == "security":
            content = self.search.search_owasp(topic)
        else:
            context = " ".join(value for value in (framework, topic) if value)
            content = self.search.search_best_practices(language, context)

        if not content:
            return None

        return RefreshPayload(
            content=content,
            source_title=metadata.get("source_title"),
            source_url=metadata.get("source_url"),
            confidence=metadata.get("confidence"),
        )


class CompositeRefreshProvider:
    def __init__(self, providers: tuple[RefreshProvider, ...]):
        self.providers = providers

    def refresh(self, document: dict[str, Any]) -> RefreshPayload | None:
        for provider in self.providers:
            payload = provider.refresh(document)
            if payload is not None:
                return payload
        return None


def build_update_plan(
    documents: list[dict[str, Any]],
    provider: RefreshProvider | None = None,
    today: date | str | None = None,
) -> UpdatePlan:
    errors = validate_seed_documents(documents)
    if errors:
        raise ValueError("seed validation failed: " + "; ".join(errors))

    current_date = _coerce_date(today) if today else date.today()
    refresh_provider = provider or NoopRefreshProvider()
    updated_documents = copy.deepcopy(documents)
    decisions = []

    for document in updated_documents:
        metadata = _metadata(document)
        cleaned_content = clean_content(str(document.get("content", "")))
        current_hash = content_hash(cleaned_content)
        document_id = str(document.get("id", ""))
        topic = str(metadata.get("topic", ""))

        if not is_expired(document, current_date):
            decisions.append(
                UpdateDecision(
                    document_id=document_id,
                    topic=topic,
                    status="fresh",
                    current_hash=current_hash,
                    reason="ttl_valid",
                )
            )
            continue

        payload = refresh_provider.refresh(document)
        if payload is None:
            decisions.append(
                UpdateDecision(
                    document_id=document_id,
                    topic=topic,
                    status="expired_no_refresh",
                    current_hash=current_hash,
                    reason="no_refresh_payload",
                )
            )
            continue

        refreshed_content = clean_content(payload.content)
        refreshed_hash = content_hash(refreshed_content)
        quality_errors = validate_refresh_payload(document, payload, refreshed_content)
        if quality_errors:
            decisions.append(
                UpdateDecision(
                    document_id=document_id,
                    topic=topic,
                    status="refresh_rejected",
                    current_hash=current_hash,
                    refreshed_hash=refreshed_hash,
                    reason="; ".join(quality_errors),
                )
            )
            continue

        _apply_payload_metadata(metadata, payload)
        metadata["last_updated"] = current_date.isoformat()
        metadata["content_hash"] = refreshed_hash
        if refreshed_hash == current_hash:
            document["content"] = cleaned_content
            decisions.append(
                UpdateDecision(
                    document_id=document_id,
                    topic=topic,
                    status="timestamp_updated",
                    current_hash=current_hash,
                    refreshed_hash=refreshed_hash,
                    reason="content_hash_unchanged",
                )
            )
            continue

        document["content"] = refreshed_content
        decisions.append(
            UpdateDecision(
                document_id=document_id,
                topic=topic,
                status="content_changed",
                current_hash=current_hash,
                refreshed_hash=refreshed_hash,
                reason="content_hash_changed",
            )
        )

    return UpdatePlan(
        generated_at=current_date.isoformat(),
        documents=tuple(updated_documents),
        decisions=tuple(decisions),
    )


def is_expired(document: dict[str, Any], today: date | str | None = None) -> bool:
    metadata = document.get("metadata", {})
    current_date = _coerce_date(today) if today else date.today()
    last_updated = _parse_date(metadata.get("last_updated"))
    if last_updated is None:
        return True
    return (current_date - last_updated).days >= _ttl_days(metadata)


def clean_content(content: str) -> str:
    return " ".join(content.split())


def validate_refresh_payload(
    document: dict[str, Any],
    payload: RefreshPayload,
    cleaned_content: str | None = None,
) -> list[str]:
    content = cleaned_content if cleaned_content is not None else clean_content(payload.content)
    metadata = document.get("metadata", {})
    source_title = payload.source_title or metadata.get("source_title")
    source_url = payload.source_url or metadata.get("source_url")
    confidence = payload.confidence if payload.confidence is not None else metadata.get("confidence")
    errors = []

    if not content:
        errors.append("content is empty")
    elif len(content) < MIN_REFRESH_CONTENT_CHARS:
        errors.append(f"content must be >= {MIN_REFRESH_CONTENT_CHARS} chars")
    elif len(content) > MAX_SEED_CONTENT_CHARS:
        errors.append(f"content must be <= {MAX_SEED_CONTENT_CHARS} chars")

    if not isinstance(source_title, str) or not source_title.strip():
        errors.append("source_title is required")

    if not isinstance(source_url, str) or not source_url.startswith("https://"):
        errors.append("source_url must be an https URL")

    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        errors.append("confidence must be a number from 0 to 1")

    if _looks_like_raw_html(content):
        errors.append("content appears to be raw HTML")

    return errors


def write_updated_seed(
    documents: tuple[dict[str, Any], ...],
    seed_path: Path = SEED_PATH,
    output_path: Path | None = None,
) -> Path:
    target_path = output_path or seed_path
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    data["documents"] = list(documents)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return target_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh expired curated RAG seed documents by TTL and content hash."
    )
    parser.add_argument("--seed", type=Path, default=SEED_PATH)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--updates-file", type=Path)
    parser.add_argument("--use-tavily", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--today")
    args = parser.parse_args()

    provider = _build_provider(args.updates_file, args.use_tavily)
    documents = load_seed_documents(args.seed)
    plan = build_update_plan(documents, provider=provider, today=args.today)

    if args.write:
        output_path = write_updated_seed(plan.documents, args.seed, args.output)
        event = "rag_update_written"
        path_field = f" path={output_path}"
    else:
        event = "rag_update_dry_run"
        path_field = ""

    counts = _format_counts(plan.counts())
    print(
        "[RAG] "
        f"event={event} "
        f"documents={len(plan.documents)} "
        f"counts={counts} "
        f"requires_reindex={str(plan.requires_reindex).lower()}"
        f"{path_field}"
    )
    return 0


def _build_provider(
    updates_file: Path | None,
    use_tavily: bool,
) -> RefreshProvider:
    providers: list[RefreshProvider] = []
    if updates_file:
        providers.append(LocalUpdatesProvider(updates_file))
    if use_tavily:
        providers.append(TavilyRefreshProvider())
    if not providers:
        return NoopRefreshProvider()
    if len(providers) == 1:
        return providers[0]
    return CompositeRefreshProvider(tuple(providers))


def _load_updates_file(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("documents"), list):
        return {
            str(item.get("id", "")): item
            for item in data["documents"]
            if isinstance(item, dict) and item.get("id")
        }
    if isinstance(data, dict):
        return data
    raise ValueError("updates file must be an object or contain a documents list")


def _metadata(document: dict[str, Any]) -> dict[str, Any]:
    metadata = document.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError(f"{document.get('id', '<unknown>')} metadata must be an object")
    return metadata


def _apply_payload_metadata(
    metadata: dict[str, Any],
    payload: RefreshPayload,
) -> None:
    if payload.source_title:
        metadata["source_title"] = payload.source_title
    if payload.source_url:
        metadata["source_url"] = payload.source_url
    if payload.confidence is not None:
        metadata["confidence"] = float(payload.confidence)
    metadata.setdefault("ttl_days", DEFAULT_TTL_DAYS)


def _ttl_days(metadata: dict[str, Any]) -> int:
    try:
        ttl_days = int(metadata.get("ttl_days", DEFAULT_TTL_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_TTL_DAYS
    return ttl_days if ttl_days > 0 else DEFAULT_TTL_DAYS


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _coerce_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    parsed = _parse_date(value)
    if parsed is None:
        raise ValueError(f"invalid date: {value}")
    return parsed


def _looks_like_raw_html(content: str) -> bool:
    lowered = content.lower()
    return "<html" in lowered or "<body" in lowered or "<script" in lowered


def _format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ",".join(f"{status}:{count}" for status, count in sorted(counts.items()))


if __name__ == "__main__":
    raise SystemExit(main())
