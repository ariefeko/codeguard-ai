# pylint: disable=protected-access,redefined-outer-name
import json
from unittest.mock import MagicMock, call, patch

import pytest

from src.orchestration.orchestrator import (
    MAX_TOKENS_STRUCTURED,
    PROVIDER_CHAIN,
    Orchestrator,
)


@pytest.fixture
def orchestrator():
    """Hindari membuat Tavily client asli di unit test."""
    instance = Orchestrator.__new__(Orchestrator)
    instance.search = MagicMock()
    instance.rag = MagicMock()
    instance.rag.retrieve_for_context.return_value = []
    instance.rag.retrieve_for_error.return_value = []
    instance.rag.format_prompt_snippets.return_value = ""
    return instance


class TestReviewFallbackChain:
    def test_returns_first_successful_provider_response(self, orchestrator):
        orchestrator._request = MagicMock(return_value="review result")

        result = orchestrator._call_llm("prompt")

        assert result == "review result"
        orchestrator._request.assert_called_once_with("prompt", PROVIDER_CHAIN[0])

    def test_falls_back_to_next_provider(self, orchestrator):
        orchestrator._request = MagicMock(side_effect=[None, "fallback result"])

        result = orchestrator._call_llm("prompt")

        assert result == "fallback result"
        assert orchestrator._request.call_args_list == [
            call("prompt", PROVIDER_CHAIN[0]),
            call("prompt", PROVIDER_CHAIN[1]),
        ]

    def test_returns_error_message_when_all_providers_fail(self, orchestrator):
        orchestrator._request = MagicMock(return_value=None)

        result = orchestrator._call_llm("prompt")

        assert result == "Error: all providers failed."
        assert orchestrator._request.call_count == len(PROVIDER_CHAIN)


class TestStructuredFallbackChain:
    def test_accepts_first_schema_valid_response(
        self,
        orchestrator,
        valid_bug_analysis_data,
    ):
        orchestrator._request = MagicMock(
            return_value=json.dumps(valid_bug_analysis_data)
        )

        result = orchestrator._call_llm_structured("prompt")

        assert result.root_cause == valid_bug_analysis_data["root_cause"]
        orchestrator._request.assert_called_once_with(
            "prompt",
            PROVIDER_CHAIN[0],
            json_mode=True,
            max_tokens=MAX_TOKENS_STRUCTURED,
        )

    def test_falls_back_after_invalid_schema(
        self,
        orchestrator,
        valid_bug_analysis_data,
    ):
        orchestrator._request = MagicMock(
            side_effect=[
                '{"status": "COMPLETE"}',
                json.dumps(valid_bug_analysis_data),
            ]
        )

        result = orchestrator._call_llm_structured("prompt")

        assert result.affected_file == valid_bug_analysis_data["affected_file"]
        assert orchestrator._request.call_count == 2

    def test_falls_back_after_request_failure(
        self,
        orchestrator,
        valid_bug_analysis_data,
    ):
        orchestrator._request = MagicMock(
            side_effect=[None, json.dumps(valid_bug_analysis_data)]
        )

        result = orchestrator._call_llm_structured("prompt")

        assert result.status == "COMPLETE"
        assert orchestrator._request.call_count == 2

    def test_returns_none_when_all_outputs_are_invalid(self, orchestrator):
        orchestrator._request = MagicMock(return_value="not-json")

        assert orchestrator._call_llm_structured("prompt") is None
        assert orchestrator._request.call_count == len(PROVIDER_CHAIN)


class TestEntryPoints:
    def test_review_code_builds_prompt_with_search_results(self, orchestrator):
        context = {
            "changed_files": {"src/app.py": "print('ok')"},
            "related_files": {},
        }
        orchestrator._enrich_with_search = MagicMock(
            return_value={"python_security": "reference"}
        )
        orchestrator._call_llm = MagicMock(return_value="review")

        with patch(
            "src.orchestration.orchestrator.build_code_review_prompt",
            return_value="built prompt",
        ) as build_prompt:
            result = orchestrator.review_code(context)

        assert result == "review"
        build_prompt.assert_called_once_with(
            context,
            {"python_security": "reference"},
        )
        orchestrator._call_llm.assert_called_once_with("built prompt")

    def test_fix_bug_builds_prompt_and_requests_structured_output(
        self,
        orchestrator,
        bug_analysis_factory,
    ):
        context = {
            "changed_files": {"src/app.py": "raise RuntimeError()"},
            "related_files": {},
        }
        error = {
            "type": "RuntimeError",
            "message": "boom",
            "file": "src/app.py",
            "line": 1,
        }
        analysis = bug_analysis_factory(affected_file="src/app.py")
        orchestrator._search_for_error = MagicMock(
            return_value={"error_info": "reference"}
        )
        orchestrator._call_llm_structured = MagicMock(return_value=analysis)

        with patch(
            "src.orchestration.orchestrator.build_bug_fix_prompt",
            return_value="bug prompt",
        ) as build_prompt:
            result = orchestrator.fix_bug(context, error)

        assert result is analysis
        build_prompt.assert_called_once_with(
            context,
            error,
            {"error_info": "reference"},
        )
        orchestrator._call_llm_structured.assert_called_once_with("bug prompt")


class TestSearchEnrichment:
    def test_enriches_review_with_rag_and_skips_tavily(self, orchestrator):
        snippets = [object()]
        orchestrator.rag.retrieve_for_context.return_value = snippets
        orchestrator.rag.format_prompt_snippets.return_value = "Relevant curated knowledge"
        context = {
            "changed_files": {"app/Service.php": "<?php"},
            "related_files": {},
        }

        result = orchestrator._enrich_with_search(context)

        assert result == {"rag": "Relevant curated knowledge"}
        orchestrator.rag.retrieve_for_context.assert_called_once_with(context)
        orchestrator.rag.format_prompt_snippets.assert_called_once_with(snippets)
        orchestrator.search.search_best_practices.assert_not_called()
        orchestrator.search.search_owasp.assert_not_called()

    def test_falls_back_to_tavily_when_review_rag_fails(self, orchestrator):
        orchestrator.rag.retrieve_for_context.side_effect = RuntimeError("qdrant down")
        orchestrator.search.search_best_practices.return_value = "python ref"
        orchestrator.search.search_owasp.return_value = "owasp ref"
        context = {
            "changed_files": {"src/app.py": "print('ok')"},
            "related_files": {},
        }

        result = orchestrator._enrich_with_search(context)

        assert result == {
            "python_security": "python ref",
            "owasp_top10": "owasp ref",
        }

    def test_enriches_python_review_and_owasp_reference(self, orchestrator):
        orchestrator.search.search_best_practices.return_value = "python ref"
        orchestrator.search.search_owasp.return_value = "owasp ref"
        context = {
            "changed_files": {"src/app.py": "print('ok')"},
            "related_files": {},
        }

        result = orchestrator._enrich_with_search(context)

        assert result == {
            "python_security": "python ref",
            "owasp_top10": "owasp ref",
        }
        orchestrator.search.search_best_practices.assert_called_once_with(
            "Python FastAPI",
            "security best practices",
        )

    def test_enriches_php_review_with_injection_reference(self, orchestrator):
        orchestrator.search.search_best_practices.return_value = "php ref"
        orchestrator.search.search_owasp.side_effect = [
            "injection ref",
            "top ten ref",
        ]
        context = {
            "changed_files": {"app/Service.php": "<?php"},
            "related_files": {},
        }

        result = orchestrator._enrich_with_search(context)

        assert result == {
            "php_security": "php ref",
            "owasp_injection": "injection ref",
            "owasp_top10": "top ten ref",
        }

    def test_enriches_javascript_review(self, orchestrator):
        orchestrator.search.search_best_practices.return_value = "js ref"
        orchestrator.search.search_owasp.return_value = None
        context = {
            "changed_files": {"src/app.ts": "export {}"},
            "related_files": {},
        }

        result = orchestrator._enrich_with_search(context)

        assert result == {"js_security": "js ref"}
        orchestrator.search.search_best_practices.assert_called_once_with(
            "Node.js JavaScript",
            "security best practices",
        )

    def test_searches_for_error_type(self, orchestrator):
        orchestrator.search._search.return_value = "runtime reference"

        result = orchestrator._search_for_error({"type": "RuntimeError"})

        assert result == {"error_info": "runtime reference"}
        orchestrator.search._search.assert_called_once_with(
            "RuntimeError fix solution best practice"
        )

    def test_enriches_error_with_rag_and_skips_tavily(self, orchestrator):
        snippets = [object()]
        orchestrator.rag.retrieve_for_error.return_value = snippets
        orchestrator.rag.format_prompt_snippets.return_value = "Relevant bug knowledge"
        context = {
            "changed_files": {"src/app.py": "raise RuntimeError()"},
            "related_files": {},
        }
        error = {"type": "RuntimeError", "file": "src/app.py"}

        result = orchestrator._search_for_error(error, context)

        assert result == {"rag": "Relevant bug knowledge"}
        orchestrator.rag.retrieve_for_error.assert_called_once_with(error, context)
        orchestrator.rag.format_prompt_snippets.assert_called_once_with(snippets)
        orchestrator.search._search.assert_not_called()

    def test_skips_error_search_without_type(self, orchestrator):
        assert orchestrator._search_for_error({}) == {}
        orchestrator.search._search.assert_not_called()


class TestRequest:
    def test_returns_none_when_api_key_is_missing(self, orchestrator, monkeypatch):
        provider = PROVIDER_CHAIN[0]
        monkeypatch.delenv(provider["api_key"], raising=False)

        with patch("src.orchestration.orchestrator.httpx.post") as post:
            result = orchestrator._request("prompt", provider)

        assert result is None
        post.assert_not_called()

    def test_sends_json_mode_and_returns_content(
        self,
        orchestrator,
        monkeypatch,
        llm_response_factory,
    ):
        provider = PROVIDER_CHAIN[0]
        monkeypatch.setenv(provider["api_key"], "secret")
        response = llm_response_factory('{"status":"ok"}')

        with patch(
            "src.orchestration.orchestrator.httpx.post",
            return_value=response,
        ) as post:
            result = orchestrator._request(
                "prompt",
                provider,
                json_mode=True,
                max_tokens=4096,
            )

        assert result == '{"status":"ok"}'
        payload = post.call_args.kwargs["json"]
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["max_tokens"] == 4096
        assert payload["messages"] == [{"role": "user", "content": "prompt"}]

    def test_handles_openagentic_done_suffix(
        self,
        orchestrator,
        monkeypatch,
        llm_response_factory,
    ):
        provider = PROVIDER_CHAIN[0]
        monkeypatch.setenv(provider["api_key"], "secret")
        response = llm_response_factory("review", append_done=True)

        with patch(
            "src.orchestration.orchestrator.httpx.post",
            return_value=response,
        ):
            assert orchestrator._request("prompt", provider) == "review"

    def test_returns_none_for_http_error(
        self,
        orchestrator,
        monkeypatch,
        llm_response_factory,
    ):
        provider = PROVIDER_CHAIN[0]
        monkeypatch.setenv(provider["api_key"], "secret")
        response = llm_response_factory("rate limited", status_code=429)

        with patch(
            "src.orchestration.orchestrator.httpx.post",
            return_value=response,
        ):
            assert orchestrator._request("prompt", provider) is None

    def test_returns_none_for_malformed_response(
        self,
        orchestrator,
        monkeypatch,
    ):
        provider = PROVIDER_CHAIN[0]
        monkeypatch.setenv(provider["api_key"], "secret")
        response = MagicMock(status_code=200, text="not-json")

        with patch(
            "src.orchestration.orchestrator.httpx.post",
            return_value=response,
        ):
            assert orchestrator._request("prompt", provider) is None

    def test_returns_none_for_network_exception(
        self,
        orchestrator,
        monkeypatch,
    ):
        provider = PROVIDER_CHAIN[0]
        monkeypatch.setenv(provider["api_key"], "secret")

        with patch(
            "src.orchestration.orchestrator.httpx.post",
            side_effect=RuntimeError("network down"),
        ):
            assert orchestrator._request("prompt", provider) is None
