"""
Output contract for Sentry bug analysis.
This is executable validation rather than documentation. The orchestrator
invokes it after every LLM response regardless of provider.
"""
import json
from typing import Literal
from pydantic import BaseModel, Field


class InferenceClaim(BaseModel):
    claim: str
    confidence: Literal["high", "medium", "low"]
    basis: str  # Must cite a specific input line or field rather than an unsupported claim.


class BugAnalysis(BaseModel):
    status: Literal["COMPLETE", "PARTIAL", "INSUFFICIENT_DATA"]
    root_cause: str
    affected_file: str
    affected_line: int | None = None
    fix_steps: str
    quick_fix_code: str
    prevention: str
    inferences: list[InferenceClaim] = Field(default_factory=list)
    # Required when status is INSUFFICIENT_DATA.
    insufficient_data_reason: str | None = None


def parse_llm_envelope(raw_response_text: str) -> dict | None:
    """
    Called by the orchestrator immediately after the HTTP request and before
    any other processing. Handles an OpenAgentic behavior that appends the SSE
    terminator "data: [DONE]" after the JSON envelope even for non-streaming
    requests, confirmed with DeepSeek V4 Flash, GLM-5, and Groq Llama 3.3 on
    June 18, 2026.

    Return None when the envelope itself is malformed.
    """
    text = raw_response_text.strip()
    if text.endswith("data: [DONE]"):
        text = text[: -len("data: [DONE]")].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def extract_content(envelope: dict) -> str | None:
    """Extract the content field from an OpenAI-compatible response structure."""
    try:
        return envelope["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def validate_llm_output(raw_text: str) -> BugAnalysis | None:
    """
    Validate content already extracted by the orchestrator. The raw HTTP
    response envelope is handled by parse_llm_envelope() and extract_content().
    Return None on validation failure to trigger the next provider fallback.
    """
    # Handle providers without native JSON mode and providers that retain a
    # Markdown fence despite response_format: json_object. This behavior was
    # confirmed with GLM-5.
    cleaned = raw_text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json")[1].split("```")[0].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(cleaned)
        return BugAnalysis(**data)
    except (json.JSONDecodeError, Exception):
        return None
