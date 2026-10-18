"""Application entry point.  Run:  uvicorn app.main:app --reload"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.logging_config import configure_logging
from app.roadmap import NAV_PHASES
from app.routers import finance, health, pages
from app.templating import templates

log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    s = get_settings()
    configure_logging(s)
    log.info("Starting %s v%s (%s)", s.app_name, s.app_version, s.environment)
    yield
    log.info("Shutting down")


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, version=s.app_version, debug=s.debug, lifespan=lifespan)

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    app.include_router(health.router)
    app.include_router(finance.router)  # before pages: /finance must not hit /{slug}
    app.include_router(pages.router)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        wants_html = "text/html" in request.headers.get("accept", "")
        if wants_html:
            return templates.TemplateResponse(
                request,
                "error.html",
                {"settings": s, "nav": NAV_PHASES, "active": "", "code": exc.status_code,
                 "message": "That page doesn't exist." if exc.status_code == 404 else "Something went wrong."},
                status_code=exc.status_code,
            )
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)

    return app


app = create_app()
