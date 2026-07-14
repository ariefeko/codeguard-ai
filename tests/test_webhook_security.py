from unittest.mock import AsyncMock

import pytest
from fastapi import Request
from fastapi.responses import JSONResponse

from src.api.main import app
from src.api.webhook_security import (
    WebhookBodySizeLimitMiddleware,
    WebhookRateLimitMiddleware,
)


def make_request(path: str, client: str = "192.0.2.1") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": (client, 1234),
            "server": ("testserver", 443),
        }
    )


@pytest.mark.asyncio
async def test_webhook_rate_limit_rejects_excess_requests_per_client():
    middleware = WebhookRateLimitMiddleware(
        AsyncMock(),
        limit=2,
        window_seconds=60,
        clock=lambda: 100.0,
    )
    call_next = AsyncMock(return_value=JSONResponse({"status": "accepted"}))

    github_response = await middleware.dispatch(
        make_request("/webhook/github"), call_next
    )
    sentry_response = await middleware.dispatch(
        make_request("/webhook/sentry"), call_next
    )

    response = await middleware.dispatch(make_request("/webhook/github"), call_next)

    assert github_response.status_code == 200
    assert sentry_response.status_code == 200
    assert response.status_code == 429
    assert response.headers["Retry-After"] == "60"
    assert call_next.await_count == 2


def test_security_middleware_is_registered_on_application():
    middleware_classes = {item.cls for item in app.user_middleware}

    assert WebhookRateLimitMiddleware in middleware_classes
    assert WebhookBodySizeLimitMiddleware in middleware_classes


@pytest.mark.asyncio
async def test_webhook_rate_limit_does_not_apply_to_other_paths():
    middleware = WebhookRateLimitMiddleware(
        AsyncMock(),
        limit=1,
        window_seconds=60,
        clock=lambda: 100.0,
    )
    call_next = AsyncMock(return_value=JSONResponse({"status": "ok"}))

    first = await middleware.dispatch(make_request("/health"), call_next)
    second = await middleware.dispatch(make_request("/health"), call_next)

    assert first.status_code == 200
    assert second.status_code == 200
    assert call_next.await_count == 2


def make_scope(path: str, headers: list[tuple[bytes, bytes]] | None = None) -> dict:
    return {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers or [],
        "client": ("192.0.2.1", 1234),
        "server": ("testserver", 443),
    }


@pytest.mark.asyncio
async def test_webhook_body_limit_rejects_oversized_content_length():
    app = AsyncMock()
    middleware = WebhookBodySizeLimitMiddleware(app, max_body_size=10)
    receive = AsyncMock()
    send = AsyncMock()

    await middleware(
        make_scope("/webhook/github", [(b"content-length", b"11")]),
        receive,
        send,
    )

    assert send.await_args_list[0].args[0]["status"] == 413
    app.assert_not_awaited()
    receive.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_body_limit_counts_streamed_chunks():
    async def body_reader(scope, receive, send):
        while True:
            message = await receive()
            if not message.get("more_body", False):
                break

    messages = iter(
        (
            {"type": "http.request", "body": b"123456", "more_body": True},
            {"type": "http.request", "body": b"78901", "more_body": False},
        )
    )

    async def receive():
        return next(messages)

    send = AsyncMock()
    middleware = WebhookBodySizeLimitMiddleware(body_reader, max_body_size=10)

    await middleware(make_scope("/webhook/sentry"), receive, send)

    assert send.await_args_list[0].args[0]["status"] == 413
