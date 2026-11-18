import logging

from fastapi import Request
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(self, type: str, title: str, detail: str = "", status: int = 400) -> None:
        super().__init__(f"{type}: {detail or title}")
        self.type, self.title, self.detail, self.status = type, title, detail, status

    def to_dict(self) -> dict:
        return {"type": self.type, "title": self.title, "detail": self.detail}


async def app_error_handler(_req: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    return JSONResponse(exc.to_dict(), status_code=exc.status, media_type="application/problem+json")


async def unhandled_error_handler(_req: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error: %s", exc)
    body = {"type": "internal_error", "title": "Internal server error", "detail": ""}
    return JSONResponse(body, status_code=500, media_type="application/problem+json")
