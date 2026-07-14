from src.config import (
    CODEGUARD_APP_ID,
    CODEGUARD_REPOSITORY_URL,
    read_positive_float_env,
    read_positive_int_env,
)
from src.utils.formatters import format_pr_comment


def test_codeguard_identity_is_shared_with_formatter():
    assert CODEGUARD_APP_ID == "codeguard-ai"
    assert CODEGUARD_REPOSITORY_URL.endswith(f"/{CODEGUARD_APP_ID}")
    assert CODEGUARD_REPOSITORY_URL in format_pr_comment("review")


def test_reads_positive_timeout_from_environment(monkeypatch):
    monkeypatch.setenv("TEST_TIMEOUT_SECONDS", "12.5")

    assert read_positive_float_env("TEST_TIMEOUT_SECONDS", 10.0) == 12.5


def test_invalid_timeout_uses_default(monkeypatch):
    monkeypatch.setenv("TEST_TIMEOUT_SECONDS", "not-a-number")

    assert read_positive_float_env("TEST_TIMEOUT_SECONDS", 10.0) == 10.0


def test_non_positive_timeout_uses_default(monkeypatch):
    monkeypatch.setenv("TEST_TIMEOUT_SECONDS", "0")

    assert read_positive_float_env("TEST_TIMEOUT_SECONDS", 10.0) == 10.0


def test_reads_positive_job_timeout_from_environment(monkeypatch):
    monkeypatch.setenv("TEST_JOB_TIMEOUT_SECONDS", "240")

    assert read_positive_int_env("TEST_JOB_TIMEOUT_SECONDS", 120) == 240


def test_invalid_job_timeout_uses_default(monkeypatch):
    monkeypatch.setenv("TEST_JOB_TIMEOUT_SECONDS", "invalid")

    assert read_positive_int_env("TEST_JOB_TIMEOUT_SECONDS", 120) == 120
