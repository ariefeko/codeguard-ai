import hashlib
import hmac

from src.agents.sentry_agent import SentryAgent


class TestVerifySignature:
    def test_accepts_valid_hmac_signature(self, monkeypatch):
        monkeypatch.setenv("SENTRY_CLIENT_SECRET", "test-secret")
        body = b'{"action":"created"}'
        signature = hmac.new(
            b"test-secret",
            body,
            hashlib.sha256,
        ).hexdigest()

        assert SentryAgent().verify_signature(body, signature) is True

    def test_rejects_invalid_signature(self, monkeypatch):
        monkeypatch.setenv("SENTRY_CLIENT_SECRET", "test-secret")

        assert SentryAgent().verify_signature(b"payload", "wrong") is False

    def test_rejects_malformed_signature_header(self, monkeypatch):
        monkeypatch.setenv("SENTRY_CLIENT_SECRET", "test-secret")

        assert SentryAgent().verify_signature(b"payload", "not-a-hex-digest") is False

    def test_rejects_missing_signature_header(self, monkeypatch):
        monkeypatch.setenv("SENTRY_CLIENT_SECRET", "test-secret")

        assert SentryAgent().verify_signature(b"payload", None) is False

    def test_rejects_when_secret_is_missing(self, monkeypatch):
        monkeypatch.delenv("SENTRY_CLIENT_SECRET", raising=False)

        assert SentryAgent().verify_signature(b"payload", "signature") is False


class TestParseIssuePayload:
    def test_parses_confirmed_issue_payload(self, sample_sentry_issue_payload):
        result = SentryAgent().parse_error(sample_sentry_issue_payload)

        assert result["type"] == "ModelNotFoundException"
        assert result["message"] == "No query results for model"
        assert result["file"] == "app/Services/BillingService.php"
        assert result["line"] == 87
        assert result["issue_id"] == "123456"
        assert set(result["related_file_paths"]) == {
            "app/Repositories/InvoiceRepository.php",
            "app/Services/BillingService.php",
        }

    def test_prefers_line_number_from_metadata(self, sample_sentry_issue_payload):
        issue_data = sample_sentry_issue_payload["data"]["issue"]["data"]
        issue_data["metadata"]["lineno"] = 12

        result = SentryAgent().parse_error(sample_sentry_issue_payload)

        assert result["line"] == 12

    def test_uses_metadata_file_when_stacktrace_is_missing(self):
        payload = {
            "data": {
                "issue": {
                    "id": "77",
                    "data": {
                        "metadata": {
                            "type": "RuntimeError",
                            "value": "boom",
                            "filename": "/src/service.py",
                            "line": 20,
                        }
                    },
                }
            }
        }

        result = SentryAgent().parse_error(payload)

        assert result == {
            "type": "RuntimeError",
            "message": "boom",
            "file": "src/service.py",
            "line": 20,
            "related_file_paths": ["src/service.py"],
            "issue_id": "77",
        }

    def test_falls_back_to_exception_when_metadata_has_no_filename(self):
        payload = {
            "data": {
                "issue": {
                    "id": "88",
                    "data": {
                        "metadata": {"type": "ValueError"},
                        "exception": {
                            "values": [
                                {
                                    "type": "ValueError",
                                    "value": "invalid value",
                                    "stacktrace": {
                                        "frames": [
                                            {
                                                "filename": "/src/controller.py",
                                                "lineno": 33,
                                                "in_app": True,
                                            }
                                        ]
                                    },
                                }
                            ]
                        },
                    },
                }
            }
        }

        result = SentryAgent().parse_error(payload)

        assert result["file"] == "src/controller.py"
        assert result["line"] == 33
        assert result["issue_id"] == "88"

    def test_returns_none_for_issue_state_change_without_error_detail(self):
        payload = {"data": {"issue": {"id": "99", "status": "resolved"}}}

        assert SentryAgent().parse_error(payload) is None


class TestParseFallbackPayloads:
    def test_parses_error_resource_and_selects_last_in_app_frame(self):
        payload = {
            "data": {
                "error": {
                    "exception": {
                        "values": [
                            {
                                "type": "TypeError",
                                "value": "bad operand",
                                "stacktrace": {
                                    "frames": [
                                        {
                                            "filename": "/src/first.py",
                                            "lineno": 10,
                                            "in_app": True,
                                        },
                                        {
                                            "filename": "/src/second.py",
                                            "lineno": 20,
                                            "in_app": True,
                                        },
                                    ]
                                },
                            }
                        ]
                    }
                }
            }
        }

        result = SentryAgent().parse_error(payload)

        assert result["type"] == "TypeError"
        assert result["file"] == "src/second.py"
        assert result["line"] == 20
        assert result["issue_id"] == ""

    def test_error_resource_without_exception_returns_none(self):
        assert SentryAgent().parse_error({"data": {"error": {}}}) is None

    def test_parses_event_resource_and_issue_id(self):
        payload = {
            "data": {
                "event": {
                    "issue_id": "event-123",
                    "exception": {
                        "values": [
                            {
                                "type": "KeyError",
                                "value": "missing key",
                                "stacktrace": {
                                    "frames": [
                                        {
                                            "filename": "/src/handler.py",
                                            "lineno": 55,
                                            "in_app": True,
                                        }
                                    ]
                                },
                            }
                        ]
                    },
                }
            }
        }

        result = SentryAgent().parse_error(payload)

        assert result["type"] == "KeyError"
        assert result["file"] == "src/handler.py"
        assert result["issue_id"] == "event-123"

    def test_event_without_stacktrace_falls_back_to_title(self):
        payload = {
            "data": {
                "event": {
                    "title": "Database connection failed",
                    "metadata": {"type": "ConnectionError"},
                }
            }
        }

        result = SentryAgent().parse_error(payload)

        assert result["type"] == "ConnectionError"
        assert result["message"] == "Database connection failed"
        assert result["file"] == ""
        assert result["line"] is None
        assert result["related_file_paths"] == []

    def test_event_without_exception_or_title_returns_none(self):
        assert SentryAgent().parse_error({"data": {"event": {}}}) is None

    def test_unknown_payload_returns_none(self):
        assert SentryAgent().parse_error({"data": {"installation": {}}}) is None
