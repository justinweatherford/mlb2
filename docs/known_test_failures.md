# Known Pre-Existing Test Failures

These 7 tests fail in the current codebase and are **not related** to opp_weak or the
pregame brain lane work. They are pre-existing failures that do not block the opp_weak
integration or paper tracking.

Do not spend time fixing these unless the task explicitly targets them.

## Failing Tests (as of 2026-06-23)

| Test | File | Likely Cause |
|------|------|--------------|
| `test_final_game_watcher_scanned_no_candidates` | `tests/test_candidate_generator.py` | Live-game watcher integration; requires running game state |
| `test_cycle_spread_markets_discovered_counted` | `tests/test_derivative_support_matrix.py` | Spread market discovery; DB/fixture state mismatch |
| `test_cycle_markets_by_derivative_type_populated` | `tests/test_derivative_support_matrix.py` | Derivative type enumeration; DB/fixture state mismatch |
| `test_run_one_cycle_with_active_game` | `tests/test_integration_g.py` | Full-cycle integration; requires game state fixture |
| (3 additional `test_integration_g.py` tests) | `tests/test_integration_g.py` | Same root cause as above |

**Total:** 7 failed, 3620 passed (baseline as of 2026-06-23)

## How to Check

```
python -m pytest --tb=no -q 2>&1 | tail -20
```

## What Does NOT Fail

All opp_weak tests pass:
- `tests/test_opp_weak_pregame_report.py` (42 tests)
- `tests/test_opp_weak_api.py` (new — 11 tests)
