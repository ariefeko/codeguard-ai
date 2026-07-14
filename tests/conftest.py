import json
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from src.orchestration.schemas import BugAnalysis


@pytest.fixture
def sample_sentry_issue_payload():
    """Build a payload that follows the primary data.issue.data.* path."""
    return {
        "action": "created",
        "data": {
            "issue": {
                "id": "123456",
                "data": {
                    "metadata": {
                        "type": "ModelNotFoundException",
                        "value": "No query results for model",
                        "filename": "/app/Services/BillingService.php",
                    },
                    "exception": {
                        "values": [
                            {
                                "type": "ModelNotFoundException",
                                "value": "No query results for model",
                                "stacktrace": {
                                    "frames": [
                                        {
                                            "filename": "/vendor/framework/Model.php",
                                            "lineno": 99,
                                            "in_app": False,
                                        },
                                        {
                                            "filename": "/app/Repositories/InvoiceRepository.php",
                                            "lineno": 41,
                                            "in_app": True,
                                        },
                                        {
                                            "filename": "/app/Services/BillingService.php",
                                            "lineno": 87,
                                            "in_app": True,
                                        },
                                    ]
                                },
                            }
                        ]
                    },
                },
            }
        },
    }


@pytest.fixture
def valid_bug_analysis_data():
    return {
        "status": "COMPLETE",
        "root_cause": "The invoice lookup assumes the record always exists.",
        "affected_file": "app/Services/BillingService.php",
        "affected_line": 87,
        "fix_steps": "Handle the missing model before using the invoice.",
        "quick_fix_code": "$invoice = Invoice::find($id);",
        "prevention": "Add missing-record tests and explicit error handling.",
        "inferences": [
            {
                "claim": "The lookup result is used without a null guard.",
                "confidence": "high",
                "basis": "BillingService.php line 87",
            }
        ],
        "insufficient_data_reason": None,
    }


@pytest.fixture
def bug_analysis_factory(
    valid_bug_analysis_data: dict[str, object],
) -> Callable[..., BugAnalysis]:
    def factory(**overrides: object) -> BugAnalysis:
        data = {**valid_bug_analysis_data, **overrides}
        return BugAnalysis(**data)

    return factory


@pytest.fixture
def llm_response_factory():
    """Build an OpenAI-compatible response without making an HTTP request."""
    def factory(content, status_code=200, append_done=False):
        response = MagicMock()
        response.status_code = status_code
        if status_code == 200:
            response.text = json.dumps(
                {"choices": [{"message": {"content": content}}]}
            )
            if append_done:
                response.text += "data: [DONE]"
        else:
            response.text = str(content)
        return response

    return factory
