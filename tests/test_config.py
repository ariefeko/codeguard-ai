from src.config import read_positive_float_env


def test_reads_positive_timeout_from_environment(monkeypatch):
    monkeypatch.setenv("TEST_TIMEOUT_SECONDS", "12.5")

    assert read_positive_float_env("TEST_TIMEOUT_SECONDS", 10.0) == 12.5


def test_invalid_timeout_uses_default(monkeypatch):
    monkeypatch.setenv("TEST_TIMEOUT_SECONDS", "not-a-number")

    assert read_positive_float_env("TEST_TIMEOUT_SECONDS", 10.0) == 10.0


def test_non_positive_timeout_uses_default(monkeypatch):
    monkeypatch.setenv("TEST_TIMEOUT_SECONDS", "0")

    assert read_positive_float_env("TEST_TIMEOUT_SECONDS", 10.0) == 10.0
