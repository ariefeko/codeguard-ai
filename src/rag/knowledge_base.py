import json
from pathlib import Path
from typing import Any

from src.rag.topic_mapper import TOPIC_PARENT_CATEGORY, TopicMapper, TopicSelection


SEED_PATH = Path(__file__).parent / "seeds" / "mvp_seed.json"
MAX_SEED_CONTENT_CHARS = 2000

REQUIRED_DOCUMENT_FIELDS = {"id", "collection", "content", "metadata"}
REQUIRED_METADATA_FIELDS = {
    "topic",
    "category",
    "language",
    "framework",
    "source_title",
    "source_url",
    "confidence",
}


def load_seed_documents(path: Path | None = None) -> list[dict[str, Any]]:
    seed_path = path or SEED_PATH
    data = json.loads(seed_path.read_text(encoding="utf-8"))
    documents = data.get("documents", [])
    if not isinstance(documents, list):
        raise ValueError("seed documents must be a list")
    return documents


def validate_seed_documents(documents: list[dict[str, Any]]) -> list[str]:
    errors = []
    seen_ids = set()
    mapper = TopicMapper()

    for index, document in enumerate(documents):
        prefix = f"documents[{index}]"
        errors.extend(_validate_document_shape(prefix, document))
        if errors and not isinstance(document, dict):
            continue

        document_id = document.get("id")
        if document_id in seen_ids:
            errors.append(f"{prefix}.id is duplicated")
        seen_ids.add(document_id)

        metadata = document.get("metadata", {})
        topic = metadata.get("topic")
        category = metadata.get("category")
        language = metadata.get("language")
        collection = document.get("collection")

        expected_category = TOPIC_PARENT_CATEGORY.get(topic)
        if expected_category and category != expected_category:
            errors.append(
                f"{prefix}.metadata.category must be {expected_category} for {topic}"
            )

        allowed_collections = mapper.collections_for(str(category), str(language))
        if collection not in allowed_collections:
            errors.append(
                f"{prefix}.collection {collection} is not valid for {category}/{language}"
            )

    return errors


def documents_for_selection(
    selection: TopicSelection,
    documents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    matches = []
    for document in documents:
        metadata = document.get("metadata", {})
        if document.get("collection") not in selection.collections:
            continue
        if metadata.get("topic") not in selection.topics:
            continue
        if metadata.get("category") != selection.category:
            continue
        if (
            selection.language != "unknown"
            and metadata.get("language") != selection.language
        ):
            continue
        if (
            selection.framework != "unknown"
            and metadata.get("framework") != selection.framework
        ):
            continue
        matches.append(document)
    return matches


def _validate_document_shape(prefix: str, document: Any) -> list[str]:
    if not isinstance(document, dict):
        return [f"{prefix} must be an object"]

    errors = []
    missing_document_fields = REQUIRED_DOCUMENT_FIELDS - set(document)
    if missing_document_fields:
        errors.append(f"{prefix} missing fields: {sorted(missing_document_fields)}")

    content = document.get("content")
    if not isinstance(content, str) or not content.strip():
        errors.append(f"{prefix}.content must be a non-empty string")
    elif len(content) > MAX_SEED_CONTENT_CHARS:
        errors.append(f"{prefix}.content must be <= {MAX_SEED_CONTENT_CHARS} chars")

    metadata = document.get("metadata")
    if not isinstance(metadata, dict):
        errors.append(f"{prefix}.metadata must be an object")
        return errors

    missing_metadata_fields = REQUIRED_METADATA_FIELDS - set(metadata)
    if missing_metadata_fields:
        errors.append(
            f"{prefix}.metadata missing fields: {sorted(missing_metadata_fields)}"
        )

    if metadata.get("topic") not in TOPIC_PARENT_CATEGORY:
        errors.append(f"{prefix}.metadata.topic is not known to TopicMapper")

    confidence = metadata.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        errors.append(f"{prefix}.metadata.confidence must be a number from 0 to 1")

    source_url = metadata.get("source_url")
    if not isinstance(source_url, str) or not source_url.startswith("https://"):
        errors.append(f"{prefix}.metadata.source_url must be an https URL")

    source_title = metadata.get("source_title")
    if not isinstance(source_title, str) or not source_title.strip():
        errors.append(f"{prefix}.metadata.source_title must be a non-empty string")

    return errors
