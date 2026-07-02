"""
src/orchestration/schemas.py

Output contract untuk analisis bug Sentry.
Ini BUKAN dokumentasi — ini validator yang dipanggil orchestrator
setelah tiap response LLM, terlepas dari provider mana yang jawab.
"""
import json
from typing import Literal
from pydantic import BaseModel, Field


class InferenceClaim(BaseModel):
    claim: str
    confidence: Literal["high", "medium", "low"]
    basis: str  # wajib merujuk baris/field spesifik dari input, bukan klaim mengambang


class BugAnalysis(BaseModel):
    status: Literal["COMPLETE", "PARTIAL", "INSUFFICIENT_DATA"]
    root_cause: str
    affected_file: str
    affected_line: int | None = None
    fix_steps: str
    quick_fix_code: str
    prevention: str
    inferences: list[InferenceClaim] = Field(default_factory=list)
    insufficient_data_reason: str | None = None  # wajib diisi kalau status INSUFFICIENT_DATA


def parse_llm_envelope(raw_response_text: str) -> dict | None:
    """
    Dipanggil DI ORCHESTRATOR, langsung setelah `requests.post(...)`, SEBELUM
    apa pun lain diproses. Khusus menangani bug OpenAgentic yang nempelin
    SSE terminator "data: [DONE]" langsung setelah penutup JSON envelope,
    bahkan untuk non-streaming request -- dikonfirmasi via testing terhadap
    DeepSeek V4 Flash, GLM-5, dan Groq Llama 3.3 (18 Jun 2026).

    Return None kalau envelope-nya sendiri rusak total (bukan cuma soal DONE).
    """
    text = raw_response_text.strip()
    if text.endswith("data: [DONE]"):
        text = text[: -len("data: [DONE]")].strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def extract_content(envelope: dict) -> str | None:
    """Ambil field content dari struktur OpenAI-compatible response."""
    try:
        return envelope["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def validate_llm_output(raw_text: str) -> BugAnalysis | None:
    """
    Dipanggil orchestrator dengan CONTENT yang sudah diekstrak (bukan raw
    HTTP response envelope -- itu sudah ditangani parse_llm_envelope() +
    extract_content() di atas).
    Return None kalau validasi gagal -> trigger retry/fallback ke provider berikutnya.
    """
    # Handle providers yang tidak support JSON mode native, atau yang tetap
    # membungkus output dengan markdown fence meski response_format sudah diset
    # (dikonfirmasi terjadi pada GLM-5 meski response_format: json_object aktif).
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
