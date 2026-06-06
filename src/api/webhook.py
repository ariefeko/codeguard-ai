from fastapi import APIRouter, Request

router = APIRouter()


@router.post("/webhook/github")
async def github_webhook(request: Request):
    payload = await request.json()

    print("\n=== GITHUB WEBHOOK RECEIVED ===")
    print(payload)

    return {
        "status": "received"
    }


@router.post("/webhook/sentry")
async def sentry_webhook(request: Request):
    payload = await request.json()

    print("\n=== SENTRY WEBHOOK RECEIVED ===")
    import json

    print(
        json.dumps( payload, indent=2, ensure_ascii=False )
    )

    return {
        "status": "received"
    }