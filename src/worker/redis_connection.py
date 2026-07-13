import os

import redis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry
from rq import Queue

from src.config import (
    REDIS_RETRY_ATTEMPTS,
    REDIS_RETRY_BASE_DELAY_SECONDS,
    REDIS_RETRY_MAX_DELAY_SECONDS,
)


REDIS_URL_SCHEMES = ("redis://", "rediss://", "unix://")


class RedisConfigurationError(RuntimeError):
    """Sanitized Redis configuration failure safe for logs and tracebacks."""

    MESSAGES = {
        "invalid_url": (
            "Redis connection URL is invalid. See the deployment documentation "
            "for supported configuration formats."
        ),
        "missing": (
            "Redis connection is not configured. See the deployment documentation "
            "for supported Railway Redis settings."
        ),
    }

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(self.MESSAGES.get(reason, "Redis configuration is invalid."))


def get_redis_url() -> str:
    for name in ("REDIS_URL", "REDIS_PRIVATE_URL", "REDIS_PUBLIC_URL"):
        value = os.getenv(name)
        if not value:
            continue

        value = value.strip()
        if value.startswith(REDIS_URL_SCHEMES):
            return value

        raise RedisConfigurationError("invalid_url")

    host = os.getenv("REDISHOST") or os.getenv("REDIS_HOST")
    port = os.getenv("REDISPORT") or os.getenv("REDIS_PORT") or "6379"
    password = os.getenv("REDISPASSWORD") or os.getenv("REDIS_PASSWORD")

    if host:
        # The constructed URL may contain the Redis password; never log it.
        auth = f":{password}@" if password else ""
        return f"redis://{auth}{host}:{port}"

    raise RedisConfigurationError("missing")


def get_redis_connection() -> redis.Redis:
    return redis.from_url(
        get_redis_url(),
        socket_connect_timeout=float(
            os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT_SECONDS", "5")
        ),
        socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT_SECONDS", "5")),
        retry=Retry(
            ExponentialBackoff(
                cap=REDIS_RETRY_MAX_DELAY_SECONDS,
                base=REDIS_RETRY_BASE_DELAY_SECONDS,
            ),
            retries=REDIS_RETRY_ATTEMPTS,
        ),
    )


def get_queue(name: str = "codeguard") -> Queue:
    return Queue(name, connection=get_redis_connection())
