"""FastAPI app factory. Run: uvicorn app.main:app --reload"""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.limits import BodySizeLimit
from app.api.plans import router
from app.core.config import get_settings
from app.core.errors import AppError, app_error_handler, unhandled_error_handler
from app.core.logging import setup_logging
from app.db.pool import close_pool, open_pool
from app.graph.build import build_graph

log = logging.getLogger("access")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    try:
        await open_pool()
    except Exception:  # noqa: BLE001 - run without DB rather than refuse to start
        logging.getLogger(__name__).exception("database unavailable at startup")
    app.state.graph = build_graph()
    yield
    await close_pool()


async def validation_handler(_req, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
    detail = f"{loc}: {first.get('msg', '')}".strip(": ")
    return JSONResponse({"type": "validation_error", "title": "Invalid request", "detail": detail},
                        status_code=422, media_type="application/problem+json")


def create_app() -> FastAPI:
    app = FastAPI(title="TravelMate AI", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BodySizeLimit)
    app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Authorization", "Content-Type"])
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)

    @app.middleware("http")
    async def access_log(request, call_next):
        t0 = time.perf_counter()
        response = await call_next(request)
        log.info("%s %s %s %dms", request.method, request.url.path, response.status_code,
                 int((time.perf_counter() - t0) * 1000))
        return response

    app.include_router(router)
    return app


app = create_app()
