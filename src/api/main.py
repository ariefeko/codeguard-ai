from fastapi import FastAPI
from dotenv import load_dotenv
from src.api.webhook import router as webhook_router
load_dotenv()

app = FastAPI(
    title="CodeGuard AI",
    version="0.1.0"
)

app.include_router(webhook_router)

@app.get("/health")
def health():
    return {
        "status": "ok"
    }