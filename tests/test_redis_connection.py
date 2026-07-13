from unittest.mock import patch

import pytest

from src.config import (
    REDIS_RETRY_ATTEMPTS,
    REDIS_RETRY_BASE_DELAY_SECONDS,
    REDIS_RETRY_MAX_DELAY_SECONDS,
)
from src.worker import redis_connection


def clear_redis_env(monkeypatch):
    for name in (
        "REDIS_URL",
        "REDIS_PRIVATE_URL",
        "REDIS_PUBLIC_URL",
        "REDISHOST",
        "REDIS_HOST",
        "REDISPORT",
        "REDIS_PORT",
        "REDISPASSWORD",
        "REDIS_PASSWORD",
    ):
        monkeypatch.delenv(name, raising=False)


def test_get_redis_url_accepts_full_redis_url(monkeypatch):
    clear_redis_env(monkeypatch)
    monkeypatch.setenv(
        "REDIS_URL",
        "redis://default:secret@redis.railway.internal:6379",
    )

    assert (
        redis_connection.get_redis_url()
        == "redis://default:secret@redis.railway.internal:6379"
    )


def test_get_redis_url_builds_from_host_port_password(monkeypatch):
    clear_redis_env(monkeypatch)
    monkeypatch.setenv("REDISHOST", "redis.railway.internal")
    monkeypatch.setenv("REDISPORT", "6379")
    monkeypatch.setenv("REDISPASSWORD", "secret")

    assert (
        redis_connection.get_redis_url()
        == "redis://:secret@redis.railway.internal:6379"
    )


def test_get_redis_url_rejects_url_without_scheme(monkeypatch):
    clear_redis_env(monkeypatch)
    invalid_url = "not-redis://default:super-secret@redis.internal:6379"
    monkeypatch.setenv("REDIS_URL", invalid_url)

    with pytest.raises(redis_connection.RedisConfigurationError) as exc_info:
        redis_connection.get_redis_url()

    message = str(exc_info.value)
    assert "Redis connection URL is invalid" in message
    assert "super-secret" not in message
    assert invalid_url not in message
    assert "REDIS_URL" not in message
    assert exc_info.value.reason == "invalid_url"
    assert exc_info.value.__context__ is None


def test_missing_redis_configuration_message_has_no_credential_template(monkeypatch):
    clear_redis_env(monkeypatch)

    with pytest.raises(redis_connection.RedisConfigurationError) as exc_info:
        redis_connection.get_redis_url()

    message = str(exc_info.value)
    assert "password" not in message.lower()
    assert "redis://" not in message
    assert "REDIS_URL" not in message
    assert exc_info.value.reason == "missing"


def test_get_redis_connection_sets_socket_timeouts_and_retry(monkeypatch):
    clear_redis_env(monkeypatch)
    monkeypatch.delenv("REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("REDIS_SOCKET_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv(
        "REDIS_URL",
        "redis://default:secret@redis.railway.internal:6379",
    )

    with patch("src.worker.redis_connection.redis.from_url") as from_url:
        redis_connection.get_redis_connection()

    from_url.assert_called_once()
    args, kwargs = from_url.call_args
    assert args == ("redis://default:secret@redis.railway.internal:6379",)
    assert kwargs["socket_connect_timeout"] == 5.0
    assert kwargs["socket_timeout"] == 5.0

    retry = kwargs["retry"]
    assert retry._retries == REDIS_RETRY_ATTEMPTS
    assert retry._backoff._base == REDIS_RETRY_BASE_DELAY_SECONDS
    assert retry._backoff._cap == REDIS_RETRY_MAX_DELAY_SECONDS


def test_get_queue_uses_configured_connection():
    connection = object()

    with patch(
        "src.worker.redis_connection.get_redis_connection",
        return_value=connection,
    ), patch("src.worker.redis_connection.Queue") as queue:
        redis_connection.get_queue("reviews")

    queue.assert_called_once_with("reviews", connection=connection)
