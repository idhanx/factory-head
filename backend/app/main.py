"""FactoryManager AI — API entry point.

A modular monolith, deliberately. Five pilot plants generate a few thousand
events a day; microservices at that scale are operational cost with no benefit
(ARCHITECTURE.md §1.2). The module boundaries here are the seams to split on
later, if the load ever justifies it.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.v1 import api_router
from app.core.config import settings
from app.core.db import dispose_engine, engine
from app.ws.manager import manager
from app.ws.routes import router as ws_router
from app.ws.routes import stop_all_tickers

logging.basicConfig(level=logging.INFO if not settings.debug else logging.DEBUG)
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
        if settings.debug
        else structlog.processors.JSONRenderer(),
    ]
)
log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await manager.startup()
    log.info("api.startup", environment=settings.environment)
    try:
        yield
    finally:
        await stop_all_tickers()
        await manager.shutdown()
        await dispose_engine()
        log.info("api.shutdown")


app = FastAPI(
    title="FactoryManager AI",
    description=(
        "The AI Plant Manager for MSME manufacturing. Every loss visible in "
        "rupees the moment it happens, and the one action that recovers the most."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(ws_router)


@app.exception_handler(ValueError)
async def value_error_handler(request: Request, exc: ValueError):
    """Domain rules raise ValueError; they are the caller's fault, not a 500."""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.get("/health")
async def health():
    """Liveness plus a real database round-trip."""
    database = "ok"
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - surfaced to the operator
        database = f"error: {exc}"

    return {
        "status": "ok" if database == "ok" else "degraded",
        "environment": settings.environment,
        "database": database,
        "llm_enabled": settings.llm_enabled,
        "mqtt_enabled": settings.mqtt_enabled,
        "whatsapp_enabled": settings.wati_enabled,
    }


@app.get("/")
async def root():
    return {
        "name": "FactoryManager AI",
        "tagline": "Machine → Money → Action",
        "docs": "/docs",
        "api": settings.api_prefix,
    }
