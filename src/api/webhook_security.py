import os
import threading
import time
from collections import deque
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send


WEBHOOK_PATHS = frozenset(("/webhook/github", "/webhook/sentry"))


class WebhookBodySizeLimitMiddleware:
    """Reject oversized webhook bodies, including chunked requests."""

    def __init__(self, app: ASGIApp, max_body_size: int | None = None) -> None:
        self.app = app
        self.max_body_size = (
            max_body_size
            if max_body_size is not None
            else int(os.getenv("WEBHOOK_MAX_BODY_SIZE_BYTES", "10000000"))
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") not in WEBHOOK_PATHS
        ):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length:
            try:
                declared_size = int(content_length)
            except ValueError:
                declared_size = 0
            if declared_size > self.max_body_size:
                await self._reject(scope, receive, send)
                return

        received_size = 0

        async def limited_receive() -> Message:
            nonlocal received_size
            message = await receive()
            if message["type"] == "http.request":
                received_size += len(message.get("body", b""))
                if received_size > self.max_body_size:
                    raise _WebhookBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _WebhookBodyTooLarge:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={"status": "rejected", "reason": "request body too large"},
        )
        await response(scope, receive, send)


class _WebhookBodyTooLarge(Exception):
    pass


class WebhookRateLimitMiddleware(BaseHTTPMiddleware):
    """Apply a bounded, per-process request limit to webhook endpoints."""

    def __init__(
        self,
        app: ASGIApp,
        limit: int | None = None,
        window_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(app)
        self.limit = (
            limit if limit is not None else int(os.getenv("WEBHOOK_RATE_LIMIT", "10"))
        )
        self.window_seconds = (
            window_seconds
            if window_seconds is not None
            else float(os.getenv("WEBHOOK_RATE_LIMIT_WINDOW_SECONDS", "60"))
        )
        self._clock = clock
        self._requests: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._next_cleanup = self._clock() + self.window_seconds

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method != "POST" or request.url.path not in WEBHOOK_PATHS:
            return await call_next(request)

        client = request.client.host if request.client else "unknown"
        now = self._clock()
        cutoff = now - self.window_seconds

        with self._lock:
            if now >= self._next_cleanup:
                self._requests = {
                    key: timestamps
                    for key, timestamps in self._requests.items()
                    if timestamps and timestamps[-1] > cutoff
                }
                self._next_cleanup = now + self.window_seconds

            timestamps = self._requests.setdefault(client, deque())
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()

            if len(timestamps) >= self.limit:
                retry_after = max(1, int(timestamps[0] + self.window_seconds - now))
                return JSONResponse(
                    status_code=429,
                    content={"status": "rejected", "reason": "rate limit exceeded"},
                    headers={"Retry-After": str(retry_after)},
                )

            timestamps.append(now)

        return await call_next(request)
