"""
api/main.py — FastAPI application for the Kalshi MLB paper-trading dashboard.

Phase 1: read-only endpoints.  POST /api/ingest added in Phase 3.

Run:
    uvicorn api.main:app --reload --port 8000

Interactive docs:
    http://localhost:8000/docs
    http://localhost:8000/redoc
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import candidate_history, candidates, health, historical_patterns, ingest, kalshi_markets, live_capture_monitor, live_state_snapshot, manual_trades, market_tape, mlb, overview, paper_lifecycle, performance, positions, post_slate_report, signals, slate, slate_health, slate_monitor, slate_refresh, summary, setup_outcomes, weather_reference
from api.deps import DB_PATH
from db.schema import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Ensure schema is up to date and WAL mode is enabled once at startup.
    conn = init_db(DB_PATH)
    conn.close()
    yield


app = FastAPI(
    title="Kalshi MLB Dashboard API",
    description="Read-only backend for the paper-trading research dashboard.",
    version="0.1.0",
    lifespan=lifespan,
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
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
PREFIX = "/api"

app.include_router(overview.router,        prefix=PREFIX, tags=["overview"])
app.include_router(summary.router,         prefix=PREFIX, tags=["summary"])
app.include_router(signals.router,         prefix=PREFIX, tags=["signals"])
app.include_router(positions.router,       prefix=PREFIX, tags=["positions"])
app.include_router(candidates.router,      prefix=PREFIX, tags=["candidates"])
app.include_router(health.router,          prefix=PREFIX, tags=["health"])
app.include_router(ingest.router,          prefix=PREFIX, tags=["ingest"])
app.include_router(kalshi_markets.router,  prefix=PREFIX, tags=["kalshi"])
app.include_router(manual_trades.router, prefix=PREFIX, tags=["manual-trades"])
app.include_router(performance.router,   prefix=PREFIX, tags=["performance"])
app.include_router(slate.router,          prefix=PREFIX, tags=["slate"])
app.include_router(setup_outcomes.router, prefix=PREFIX, tags=["setup-outcomes"])
app.include_router(mlb.router, prefix=f"{PREFIX}/mlb/team-context", tags=["mlb"])
app.include_router(historical_patterns.router, prefix=PREFIX, tags=["historical-patterns"])
app.include_router(candidate_history.router,   prefix=PREFIX, tags=["historical-context"])
app.include_router(market_tape.router,         prefix=PREFIX, tags=["market-tape"])
app.include_router(slate_health.router,           prefix=PREFIX, tags=["slate-health"])
app.include_router(paper_lifecycle.router,        prefix=PREFIX, tags=["paper-lifecycle"])
app.include_router(live_capture_monitor.router,   prefix=PREFIX, tags=["live-capture"])
app.include_router(weather_reference.router,      prefix=PREFIX, tags=["weather-reference"])
app.include_router(post_slate_report.router,      prefix=PREFIX, tags=["post-slate-report"])
app.include_router(live_state_snapshot.router,    prefix=PREFIX, tags=["live-state"])
app.include_router(slate_monitor.router,          prefix=PREFIX, tags=["slate-monitor"])
app.include_router(slate_refresh.router,          prefix=PREFIX, tags=["slate-monitor"])


@app.get("/", include_in_schema=False)
def root():
    return {"status": "ok", "docs": "/docs"}
