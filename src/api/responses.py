from typing import Any

from fastapi.responses import JSONResponse


def webhook_response(
    status_code: int,
    status: str,
    reason: str,
    **data: Any,
) -> JSONResponse:
    """Build the shared response envelope used by every webhook outcome."""
    return JSONResponse(
        status_code=status_code,
        content={"status": status, "reason": reason, **data},
    )
