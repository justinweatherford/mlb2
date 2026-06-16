# Spread Recovery False Positive Risks — 2026-06-15

## Identified Risk Categories

### 1. First-Discovery Price Inflation (PRIMARY RISK)
All 1467 candidates have `baseline_source=first_discovery`.
The "initial price" used to measure compression is the first time the system
saw the market, not a calibrated pre-game fair value.

**Risk**: compression of 15-20c could be pure noise from initial price discovery,
not real game-state-driven compression. A team trailing 0-2 in the 2nd might have
the same market price as a team leading 2-0 because the market was stale.

**Mitigation before live implementation**:
- Require at least 3 pre-game snapshots to establish baseline
- Use only snapshot/historical baselines, never first_discovery
- Flag any candidate where baseline has < 5 pre-game observations

### 2. Spread Market Staleness (CRITICAL FINDING)
On 2026-06-15, spread/run-line markets were NOT repricing live during active gameplay.
DET+2 stayed at 35c from inning 1 through inning 5+ regardless of score changes.
This means "compression" signals derived from mid-game snapshots are not real-time
market information — they're stale pre-game prices.

**Risk**: A scoring model built on snapshot mid prices would identify "opportunities"
that don't actually exist as live tradeable states.

**Mitigation before live implementation**:
- Track spread market delta_mid between consecutive snapshots during gameplay
- Only consider spread markets with confirmed live repricing (delta_mid > 5c in-game)
- Monitor orderbook depth, not just mid price

### 3. Run-Line vs. Moneyline Conflation
The spread recovery thesis is about winning by N+ runs, not just winning.
A team that consistently wins close games (4-3, 5-4) may be strong on moneyline
but weak on run-line.

**Risk**: Using team_strength_rating (which reflects overall quality) as proxy
for run-line capability overstates spread recovery probability.

**Mitigation before live implementation**:
- Add a "run-line conversion rate" metric: % of wins that were by 2+ runs
- Only use teams with run-line conversion rate > 50% for spread research
- Look at average margin-of-victory, not just win probability

### 4. Insufficient Innings Buffer for Run-Line Recovery
To win by 2+, a trailing team needs both:
  a) Come back from the deficit
  b) Extend the lead to N+ runs

This is a DOUBLE requirement. A team trailing by 1 run in the 5th needing +2
actually needs a 3-run net swing (not 1+2=3, but the compound probability
is multiplicative). The linear buffer model overstates probability.

**Mitigation**:
- Use a more conservative buffer multiplier (e.g., gap × 1.5 instead of gap × 1.0)
- Or require innings_remaining >= gap_to_runline × 1.5

### 5. Active Rally Entry Risk
The spec requires checking that no active rally is happening at trigger time.
Our current implementation sets active_rally_flag=0 for all snapshot-level
candidates because we don't track per-half-inning scoring events.

**Risk**: Some candidates may have been generated during an opponent scoring
burst, making the entry price stale and the context misleading.

**Mitigation before live implementation**:
- Track `recent_scoring_flag` from mlb_play_events
- Block candidates where opponent scored within last 2 at-bats

### 6. PIT@ATH Market Anomaly
PIT@ATH spread markets showed only 1c movement despite ATH winning 11-2.
Either the market was illiquid, or we weren't capturing the live repricing.

**Risk**: Market coverage is inconsistent across games. Some games may have
stale spread markets that never reprice properly.

**Mitigation**: Track per-game spread market activity score before relying
on it for research or live signals.

## Summary Risk Table

| Risk | Severity | Candidate Impact | Mitigation Priority |
|------|----------|-----------------|---------------------|
| First-discovery inflation | CRITICAL | All 2026-06-15 | P0 — build baseline tracking |
| Spread market staleness | CRITICAL | All live states | P0 — verify live repricing |
| Run-line vs. moneyline conflation | HIGH | All candidates | P1 — add conversion rate |
| Insufficient buffer model | MEDIUM | Watch candidates | P2 — tighten buffer math |
| Active rally entry risk | MEDIUM | All candidates | P2 — add play event tracking |
| PIT@ATH market anomaly | LOW | 1 game | P3 — investigate liquidity |
