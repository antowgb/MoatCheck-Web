"""FastAPI entry point.

Also serves the statically exported Next.js frontend (``static/`` directory,
produced by ``npm run build`` on the frontend side) — a single service in prod.
"""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="Quant Stock Screener", version="0.1.0")


@app.exception_handler(Exception)
async def log_unhandled_exceptions(request: Request, exc: Exception) -> JSONResponse:
    """Logs the full traceback for any unhandled exception before returning 500.

    Without this, an unhandled exception in a route just becomes Starlette's
    generic "Internal Server Error" with nothing in the logs to diagnose it —
    this keeps that same response but makes the cause debuggable.
    """
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

origins = [o.strip() for o in os.environ.get("FRONTEND_ORIGINS", "http://localhost:3000").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Static frontend (Next.js export copied into backend/static at build time).
# Mounted last so it doesn't shadow /api and /health.
_static_dir = Path(__file__).resolve().parent.parent / "static"
if _static_dir.is_dir():
    app.mount("/", StaticFiles(directory=_static_dir, html=True), name="frontend")
else:
    logging.getLogger(__name__).info(
        "No static/ directory — frontend not served (API-only dev mode)."
    )
