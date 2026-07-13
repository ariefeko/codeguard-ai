from unittest.mock import MagicMock

import pytest

from src.context.context_builder import ContextBuilder


@pytest.fixture
def builder():
    return ContextBuilder(
        "ariefeko",
        "tagihin",
        "abc123",
        http_client=MagicMock(),
    )


def test_filter_accepts_relative_repository_paths(builder):
    assert builder._filter(["src/app.py", "app/Services/Billing.php"]) == [
        "src/app.py",
        "app/Services/Billing.php",
    ]


@pytest.mark.parametrize(
    "path",
    [
        "../secret.py",
        "src/../../secret.py",
        "src/%2e%2e/secret.py",
        "src/%252e%252e/secret.py",
        "..\\secret.py",
        "/etc/passwd.py",
        "C:\\secrets\\token.py",
        "\\\\server\\share\\file.py",
        "src/unsafe\x00.py",
        "",
        None,
    ],
)
def test_filter_rejects_suspicious_repository_paths(builder, path):
    assert builder._filter([path]) == []


def test_build_does_not_fetch_rejected_paths(builder):
    result = builder.build(["../secret.py", "/tmp/private.py"])

    assert result == {"changed_files": {}, "related_files": {}}
    builder.http_client.get.assert_not_called()
