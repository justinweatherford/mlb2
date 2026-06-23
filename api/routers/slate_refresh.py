"""
api/routers/slate_refresh.py — POST /api/mlb/slate-monitor/refresh

Triggers on-demand regeneration of Slate Monitor data files.
Each task runs the relevant Python script as a subprocess from the repo root
and returns stdout/stderr and duration so the UI can surface errors.

Tasks:
  ev_overlay   — kalshi_ev_overlay_preview.py --date {date}
  opp_weak     — opp_weak_pregame_report.py --date {date}
  health       — kalshi_snapshot_collection_health.py --slate-date {date}
  brain        — score_today_slate.py --date {date}
  paper_grade  — opp_weak_paper_grader.py --date {yesterday}
"""
import asyncio
import subprocess
import sys
import time
from datetime import date as _date, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

REPO_ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable  # same interpreter that runs uvicorn

_TASK_MAP: dict[str, dict] = {
    "ev_overlay": {
        "label":   "EV Overlay",
        "cmd":     [PY, "kalshi_ev_overlay_preview.py", "--date", "{date}"],
        "timeout": 120,
    },
    "opp_weak": {
        "label":   "Opp Weak Report",
        "cmd":     [PY, "opp_weak_pregame_report.py", "--date", "{date}"],
        "timeout": 60,
    },
    "health": {
        "label":   "Collector Health",
        "cmd":     [PY, "kalshi_snapshot_collection_health.py", "--slate-date", "{date}"],
        "timeout": 30,
    },
    "brain": {
        "label":   "Brain Scoring",
        "cmd":     [PY, "score_today_slate.py", "--date", "{date}"],
        "timeout": 90,
    },
    "paper_grade": {
        "label":   "Paper Grade",
        # grades the day before the slate date (games just finished)
        "cmd":     [PY, "opp_weak_paper_grader.py", "--date", "{yesterday}"],
        "timeout": 30,
    },
}


class RefreshRequest(BaseModel):
    task: str
    date: Optional[str] = None  # YYYY-MM-DD; defaults to today


class RefreshResponse(BaseModel):
    ok: bool
    task: str
    label: str
    output: str
    duration_ms: int
    error: Optional[str] = None


def _build_cmd(task_cfg: dict, slate_date: str) -> list[str]:
    yesterday = str(_date.fromisoformat(slate_date) - timedelta(days=1))
    return [
        c.replace("{date}", slate_date).replace("{yesterday}", yesterday)
        for c in task_cfg["cmd"]
    ]


def _run_task(cmd: list[str], timeout: int) -> tuple[bool, str]:
    """Run cmd synchronously from repo root. Returns (ok, combined_output)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"[TIMEOUT] Process exceeded {timeout}s"
    except FileNotFoundError as e:
        return False, f"[NOT FOUND] {e}"
    except Exception as e:
        return False, f"[ERROR] {e}"


@router.post("/mlb/slate-monitor/refresh", response_model=RefreshResponse)
async def slate_refresh(req: RefreshRequest) -> RefreshResponse:
    task_name = req.task
    if task_name not in _TASK_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown task '{task_name}'. Valid: {list(_TASK_MAP)}",
        )

    slate_date = req.date or str(_date.today())
    task_cfg   = _TASK_MAP[task_name]
    cmd        = _build_cmd(task_cfg, slate_date)

    t0 = time.monotonic()
    ok, output = await asyncio.to_thread(_run_task, cmd, task_cfg["timeout"])
    duration_ms = int((time.monotonic() - t0) * 1000)

    return RefreshResponse(
        ok=ok,
        task=task_name,
        label=task_cfg["label"],
        output=output,
        duration_ms=duration_ms,
        error=output if not ok else None,
    )
