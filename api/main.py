"""
api/main.py — FastAPI application for the Kalshi MLB paper-trading dashboard.

Phase 1: read-only endpoints.  POST /api/ingest added in Phase 3.

Run:
    uvicorn api.main:app --reload --port 8000

Interactive docs:
    http://localhost:8000/docs
    http://localhost:8000/redoc
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import candidates, health, ingest, kalshi_markets, positions, signals, summary

app = FastAPI(
    title="Kalshi MLB Dashboard API",
    description="Read-only backend for the paper-trading research dashboard.",
    version="0.1.0",
)

# ---------------------------------------------------------------------------
# CORS — permissive for local development.
# Vite default :5173, CRA :3000, Streamlit :8501.
# Tighten origins before any remote deploy.
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
        "http://localhost:8501",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
PREFIX = "/api"

app.include_router(summary.router,         prefix=PREFIX, tags=["summary"])
app.include_router(signals.router,         prefix=PREFIX, tags=["signals"])
app.include_router(positions.router,       prefix=PREFIX, tags=["positions"])
app.include_router(candidates.router,      prefix=PREFIX, tags=["candidates"])
app.include_router(health.router,          prefix=PREFIX, tags=["health"])
app.include_router(ingest.router,          prefix=PREFIX, tags=["ingest"])
app.include_router(kalshi_markets.router,  prefix=PREFIX, tags=["kalshi"])


@app.get("/", include_in_schema=False)
def root():
    return {"status": "ok", "docs": "/docs"}
