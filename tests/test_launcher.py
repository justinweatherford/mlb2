"""
tests/test_launcher.py — Content-only checks for dev.bat and TOMORROW_SLATE_RUNBOOK.md.

No shell execution. Tests verify required commands, window labels, date
handling, and absence of trade-execution instructions.

Groups:
  TestLauncherExists       — dev.bat present at repo root
  TestDevMode              — API + frontend commands in dev mode section
  TestSlateMode            — all 6 slate processes present
  TestWindowTitles         — required "MLB2 ..." window names present
  TestDateHandling         — DATE variable used for JSONL path and health URL
  TestDiscoverCommand      — uses --sport mlb, not bare --all flag
  TestNoTradeExecution     — no order placement / trade execution strings
  TestRunbookOneClick      — runbook references dev.bat slate + window names
"""
import os

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
BAT_PATH = os.path.join(REPO_ROOT, "dev.bat")
RUNBOOK_PATH = os.path.join(REPO_ROOT, "docs", "TOMORROW_SLATE_RUNBOOK.md")


def _bat():
    return open(BAT_PATH, encoding="utf-8", errors="replace").read()


def _runbook():
    return open(RUNBOOK_PATH, encoding="utf-8").read()


# ── TestLauncherExists ────────────────────────────────────────────────────────

class TestLauncherExists:
    def test_dev_bat_exists(self):
        assert os.path.exists(BAT_PATH), "dev.bat not found at repo root"

    def test_dev_bat_is_nonempty(self):
        assert len(_bat()) > 50


# ── TestDevMode ───────────────────────────────────────────────────────────────

class TestDevMode:
    def test_uvicorn_api_command(self):
        assert "uvicorn api.main:app" in _bat()

    def test_api_port_8000(self):
        assert "8000" in _bat()

    def test_frontend_npm_run_dev(self):
        assert "npm run dev" in _bat()

    def test_frontend_dir_used(self):
        bat = _bat()
        assert "frontend" in bat.lower()

    def test_api_docs_url_mentioned(self):
        bat = _bat()
        assert "localhost:8000" in bat


# ── TestSlateMode ─────────────────────────────────────────────────────────────

class TestSlateMode:
    def test_slate_keyword_triggers_mode(self):
        bat = _bat().lower()
        assert "slate" in bat

    def test_orderbook_recorder_present(self):
        assert "kalshi_orderbook_recorder.py" in _bat()

    def test_orderbook_recorder_interval_seconds(self):
        assert "--interval-seconds" in _bat()

    def test_orderbook_recorder_duration_minutes(self):
        assert "--duration-minutes" in _bat()

    def test_orderbook_recorder_jsonl_path(self):
        bat = _bat()
        assert "--jsonl" in bat
        assert "kalshi_orderbook_" in bat

    def test_mlb_poller_present(self):
        assert "mlb_poller.py" in _bat()

    def test_mlb_poller_interval(self):
        assert "--interval 30" in _bat()

    def test_live_watcher_present(self):
        assert "live_watcher.py" in _bat()

    def test_live_watcher_interval(self):
        assert "--interval 60" in _bat()

    def test_kalshi_discover_present(self):
        assert "kalshi_discover.py" in _bat()

    def test_health_endpoint_present(self):
        bat = _bat()
        assert "slate-health" in bat

    def test_health_endpoint_uses_date(self):
        bat = _bat()
        assert "slate-health" in bat
        # health URL should include the date variable
        assert "DATE" in bat


# ── TestWindowTitles ──────────────────────────────────────────────────────────

class TestWindowTitles:
    def test_mlb2_api_title(self):
        assert "MLB2 API" in _bat()

    def test_mlb2_frontend_title(self):
        assert "MLB2 Frontend" in _bat()

    def test_mlb2_orderbook_recorder_title(self):
        assert "MLB2 Orderbook Recorder" in _bat()

    def test_mlb2_mlb_poller_title(self):
        assert "MLB2 MLB Poller" in _bat()

    def test_mlb2_live_watcher_title(self):
        assert "MLB2 Live Watcher" in _bat()

    def test_mlb2_slate_health_title(self):
        assert "MLB2 Slate Health" in _bat()


# ── TestDateHandling ──────────────────────────────────────────────────────────

class TestDateHandling:
    def test_date_variable_set(self):
        assert "DATE" in _bat()

    def test_date_used_in_jsonl_filename(self):
        bat = _bat()
        # The JSONL filename should embed %DATE%
        assert "kalshi_orderbook_" in bat
        assert "%DATE%" in bat

    def test_date_used_in_health_url(self):
        bat = _bat()
        assert "slate-health" in bat
        assert "%DATE%" in bat

    def test_date_defaults_to_today_via_python(self):
        bat = _bat()
        # Should use Python to get today's date as default
        assert "datetime.date.today" in bat or "datetime.today" in bat

    def test_date_accepts_second_argument(self):
        bat = _bat()
        # Second command-line arg sets date
        assert "%~2" in bat


# ── TestDiscoverCommand ───────────────────────────────────────────────────────

class TestDiscoverCommand:
    def test_discover_uses_sport_mlb(self):
        assert "--sport mlb" in _bat()

    def test_discover_does_not_use_bare_all_flag(self):
        import re
        bat = _bat()
        # --all alone (not --status all or similar) should not appear as a standalone
        # The old buggy command was: kalshi_discover.py --all
        # The correct command is: kalshi_discover.py --sport mlb
        # Regex: --all followed by whitespace, quote, or end-of-line (not --status all)
        bad_pattern = re.search(r'kalshi_discover\.py[^\n]*\s--all(?:\s|"|$)', bat)
        assert bad_pattern is None, (
            f"Found bare '--all' flag on kalshi_discover.py: {bad_pattern.group()}"
        )

    def test_discover_is_blocking_before_other_slate_processes(self):
        bat = _bat()
        discover_pos = bat.find("kalshi_discover.py")
        recorder_pos = bat.find("kalshi_orderbook_recorder.py")
        # discovery must appear before recorder in the file
        assert discover_pos < recorder_pos


# ── TestNoTradeExecution ──────────────────────────────────────────────────────

class TestNoTradeExecution:
    def test_no_place_order(self):
        assert "place order" not in _bat().lower()
        assert "place_order" not in _bat().lower()

    def test_no_execute_trade(self):
        assert "execute trade" not in _bat().lower()
        assert "execute_trade" not in _bat().lower()

    def test_no_auto_trade(self):
        assert "auto-trade" not in _bat().lower()
        assert "auto_trade" not in _bat().lower()

    def test_no_buy_sell_commands(self):
        bat = _bat().lower()
        assert "buy_contract" not in bat
        assert "sell_contract" not in bat

    def test_no_order_endpoint(self):
        bat = _bat().lower()
        assert "/orders" not in bat


# ── TestRunbookOneClick ───────────────────────────────────────────────────────

class TestRunbookOneClick:
    def test_runbook_references_dev_bat(self):
        assert "dev.bat" in _runbook()

    def test_runbook_references_slate_mode(self):
        runbook = _runbook()
        assert "dev.bat slate" in runbook or ("dev.bat" in runbook and "slate" in runbook)

    def test_runbook_lists_mlb2_api_window(self):
        assert "MLB2 API" in _runbook()

    def test_runbook_lists_mlb2_frontend_window(self):
        assert "MLB2 Frontend" in _runbook()

    def test_runbook_lists_mlb2_orderbook_recorder_window(self):
        assert "MLB2 Orderbook Recorder" in _runbook()

    def test_runbook_lists_mlb2_mlb_poller_window(self):
        assert "MLB2 MLB Poller" in _runbook()

    def test_runbook_lists_mlb2_live_watcher_window(self):
        assert "MLB2 Live Watcher" in _runbook()

    def test_runbook_lists_mlb2_slate_health_window(self):
        assert "MLB2 Slate Health" in _runbook()

    def test_runbook_no_trade_execution(self):
        runbook = _runbook().lower()
        assert "place order" not in runbook
        assert "execute trade" not in runbook
        assert "auto-trade" not in runbook
