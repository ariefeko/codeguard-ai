import json
from pathlib import Path


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sentry" / "model_not_found.json"


def test_model_not_found_fixture_is_valid_sentry_error_context():
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    assert payload["exception"]["values"]
    assert payload["metadata"]["filename"]
    assert payload["metadata"]["type"].endswith("ModelNotFoundException")
