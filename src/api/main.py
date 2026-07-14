from fastapi import FastAPI
from dotenv import load_dotenv
from src.api.webhook import router as webhook_router
from src.api.webhook_security import (
    WebhookBodySizeLimitMiddleware,
    WebhookRateLimitMiddleware,
)

load_dotenv()

app = FastAPI(
    title="CodeGuard AI",
    version="0.1.0"
)

app.add_middleware(WebhookRateLimitMiddleware)
app.add_middleware(WebhookBodySizeLimitMiddleware)
app.include_router(webhook_router)

@app.get("/health")
def health():
    return {
        "status": "ok"
    }
