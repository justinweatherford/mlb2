# Market Prior Audit — MLB/Kalshi Team Totals
## 2026-06-25

---

## Question

Can we use sportsbook consensus as the primary probability anchor for Kalshi team total
contracts ([TEAM]4, [TEAM]5, [TEAM]8), with the brain as a context/filter layer?

---

## 1. Do we already have sportsbook team total data?

**SBR has game totals, not team totals.** `totals/full-game/` returns combined run total
(e.g., 8.5) with over/under vig for 6 books. It does not expose team-specific run totals.

SBR team total URL patterns probed — all return HTTP 500 (unavailable):

| URL pattern                              | Status |
|------------------------------------------|--------|
| `team-totals/full-game/`                | 500    |
| `player-props/team-totals/`             | 500    |
| `totals/team/`                          | 500    |
| `totals/team-total/`                    | 500    |
| `props/team-totals/`                    | 500    |
| `alternate-totals/full-game/`           | 500    |

**The Odds API** (`api.the-odds-api.com`) has `team_totals` as a valid market parameter —
confirmed by HTTP 401 (auth required, not 404). It's available in their paid tier. Not
evaluated further since we don't have a key.

---

## 2. What's already in our DB?

**We have 1,792 Kalshi team_total markets** already stored in `kalshi_markets`. Ticker format:

```
KXMLBTEAMTOTAL-26JUN232140ATLSD-ATL5
                 ↑date+time ↑teams  ↑team+line
```

Suffix encodes team + threshold: `ATL5` = ATL scores 5+ runs (YES).
Lines offered: 2, 3, 4, 5, 6, 7, 8.

**All 1,792 have bid/ask populated.** 920 also have `game_open_price_cents` set
(pregame price recorded at market open).

Sample Jun 23 pregame prices:
| Market | Kalshi open | Current mid |
|--------|-------------|-------------|
| ATL5 @ SD  | 44c | 43c |
| SD5 @ ATL  | 27c | — |
| ATH5 @ SF  | 45c | 45c |
| SF5 @ ATH  | 37c | — |
| PHI5 @ WSH | 52c | 50c |
| WSH5 @ PHI | 54c | 52c |

**Kalshi's own open price IS the market prior.** It already reflects sportsbook consensus,
sharp money, and team-specific history — it's more informative than any inferred proxy.

---

## 3. Can we infer team implied runs from game total + run line?

Yes. Standard approximation (verified against Kalshi):

```
If home team has run line -1.5 (is the favorite):
  home_implied_runs = (game_total + 1.5) / 2
  away_implied_runs = (game_total - 1.5) / 2

Then: P(team scores k+) = Poisson_CDF_complement(k, implied_runs)
```

Both SBR data sources needed (game totals + point spread) are available at HTTP 200.

---

## 4. Poisson vs. Kalshi Calibration (247 data points, 6 dates, Jun 16–24)

Cross-referenced Kalshi pregame open prices against Poisson inference from SBR game
total + run line for all games where both were available.

| Line | N  | Kalshi avg | Poisson avg | Mean gap (K-P) | Abs gap avg | Kalshi > Poisson |
|------|----|-----------|------------|----------------|-------------|-----------------|
| 4+   | 97 | 0.580     | 0.604      | **−2.4pp**     | 9.7pp       | 40/97 (41%)     |
| 5+   |101 | 0.463     | 0.453      | **+1.0pp**     | 9.4pp       | 50/101 (50%)    |
| 8+   | 59 | 0.174     | 0.101      | **+7.3pp**     | 7.4pp       | 57/59 (97%)     |

**Line 5+ finding**: Poisson is essentially unbiased vs. Kalshi pregame prices
(+1.0pp mean gap, 50/50 direction). The 9.4pp abs error is noise, not systematic bias.

**Line 4+ finding**: Poisson is 2.4pp high on average. Kalshi consistently prices 4+
slightly more conservatively than game-total math predicts.

**Line 8+ finding**: Poisson systematically underestimates Kalshi by 7.3pp. Baseball
run distributions have heavier tails than Poisson for high-scoring outcomes.

**Examples of large gaps (abs >15pp on 5+)**:

| Date       | Game    | Team | Kalshi | Poisson | Gap    | Likely reason |
|------------|---------|------|--------|---------|--------|---------------|
| 2026-06-22 | ATL@SD  | SD   | 0.36   | 0.138   | +0.222 | SD pitcher context |
| 2026-06-17 | MIA@PHI | PHI  | 0.50   | 0.371   | +0.129 | PHI offense quality |
| 2026-06-23 | HOU@TOR | HOU  | 0.41   | 0.574   | −0.164 | HOU cold streak |
| 2026-06-16 | NYM@CIN | NYM  | 0.57   | 0.726   | −0.156 | NYM expected output |

These large deviations are the market pricing in pitcher quality, team form, and park
factors that Poisson misses. The same factors our brain scores.

---

## 5. Design: Market-Prior Comparison Layer

For each pregame slate row on a team_total market:

| Field | Source | Description |
|-------|--------|-------------|
| `kalshi_open_price_cents` | DB (`game_open_price_cents`) | Pregame market anchor |
| `kalshi_current_ask_cents` | DB (`yes_ask_cents`) | Cost to buy YES now |
| `market_implied_p` | `kalshi_open_price_cents / 100` | Market's pregame probability |
| `sportsbook_poisson_p` | SBR total + runline + Poisson | Independent sanity check |
| `poisson_gap_pp` | `market_implied_p − sportsbook_poisson_p` | Market deviation from game-math |
| `brain_score` | `score_today_slate.py` output | Brain's run-suppression probability |
| `market_brain_gap_pp` | `market_implied_p − brain_score` | Key signal: where brain disagrees with market |
| `fill_side` | YES / NO | Which side we'd trade if gap is actionable |

**Actionable pattern** (not yet implemented): When:
- `market_implied_p` for [TEAM]5 is ≥ 0.50 (market prices 5+ as even money or better)
- `brain_score` on 5+NO for that team is ≥ 0.40 (brain thinks it's favorable to sell 5+)
- `sportsbook_poisson_p` is also below `market_implied_p` (Poisson confirms market is "high")

...this is the signal that the market overprices team scoring and the brain agrees.

---

## 6. Verdict

### Primary question: Can we use sportsbook consensus as the probability anchor?

**We cannot use SBR as the direct anchor for team totals** — SBR does not expose team
total odds. The Odds API has them behind a paid key.

**We do not need to** — we already have the market prior. Kalshi's own `game_open_price_cents`
is the primary probability anchor. It's a sharper signal than any game-total inference
because it already incorporates pitcher matchups, team form, and park factors.

### Secondary question: Is Poisson inference from game total + run line good enough as a fallback?

**For 5+ contracts: yes, as a sanity check.** Essentially unbiased (+1.0pp), 9.4pp abs error.
Use it to flag cases where Kalshi prices significantly deviate from naive game-math — those
deviations tell you whether the market is pricing pitcher quality, team momentum, or is
just stale/illiquid.

**For 8+ contracts: no.** Poisson understates by 7.3pp on average. Don't use it to
benchmark 8+ market prices.

### What we actually need to build the comparison layer:

1. **Already available** (no new data sources needed):
   - Kalshi team total open prices: in DB (`kalshi_markets.game_open_price_cents`)
   - Kalshi live mid: `(yes_bid_cents + yes_ask_cents) / 2`
   - SBR game total + run line: HTTP 200, same `__NEXT_DATA__` structure as moneylines

2. **Needs implementation** (2 pieces):
   - A function that joins Kalshi team total prices to `score_today_slate.py` output by
     team and date (ticker parsing is trivial: extract last token, e.g., `ATL5` → team=ATL, line=5)
   - A Poisson-based sanity check using SBR data (reuse existing `sbr_mlb_odds_fetcher.py` pattern)

3. **The Odds API** (optional enhancement): Get actual sportsbook team total lines with
   over/under vig removed. Upgrade from Poisson inference to real implied probability.
   Requires paid key (~$79/month for 30k requests). Recommended if the comparison layer
   becomes a primary trading input.

---

## 7. Recommendation (No Action Required Now)

**Do not add the comparison layer yet.** This is research only.

Before adding market-brain comparison to the live scorer, the brain's 5+ and 4+ lanes
must demonstrate edge in out-of-sample data. The market-prior layer makes most sense as
a *filter* (e.g., "only surface NO candidates when market also prices 5+ ≥ 50c") not as
a replacement for the brain.

Priority order when ready to implement:
1. Add `kalshi_team5_open_p` join to slate output (uses existing DB data)
2. Compute `market_brain_gap_pp = kalshi_p - brain_score` per row
3. Flag rows where gap ≥ 10pp as "market-brain divergence"
4. Back-test: does large market-brain gap predict outcomes differently than brain alone?

---

## Appendix: SBR Data Sources

| Source | URL pattern | Status | Data |
|--------|-------------|--------|------|
| Moneyline | `money-line/full-game/` | 200 | homeOdds, awayOdds per book |
| Game total | `totals/full-game/` | 200 | total, overOdds, underOdds per book |
| Run line | `pointspread/full-game/` | 200 | homeSpread, awaySpread, homeOdds, awayOdds per book |
| Team totals | `team-totals/full-game/` | **500** | Not available |
| F5 totals | `totals/first-half/` | **500** | Not available |
| F5 spread | `pointspread/first-half/` | **500** | Not available |
