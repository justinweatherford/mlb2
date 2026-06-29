# Kalshi Snapshot Collection Health

Slate date: **2026-06-25**
Checked at: 2026-06-25T16:24:49.342425+00:00
Thresholds: fresh ≤15min, recent ≤60min

## Overall Status: DEGRADED

| Metric | Count | Pct | Note |
|--------|-------|-----|------|
| Total slate markets | 535 | 100% | |
| Fresh with bid/ask (<15min) | 423 | 79.1% | Collector running, MM active |
| Fresh empty book (<15min) | 0 | 0.0% | Collector running, no MM yet (expected pre-game) |
| Recent with bid/ask (<60min) | 0 | 0.0% | |
| Recent empty book (<60min) | 0 | 0.0% | Collector running, no MM yet |
| Stale with bid/ask (>60min) | 0 | 0.0% | |
| Stale empty book (>60min) | 0 | 0.0% | Old snap, check if collector stopped |
| No snapshots | 112 | 20.9% | |
| Not polled (player_hr) | 112 | 20.9% | Intentionally excluded |

## Priority Markets (EV Overlay Lanes)
Priority types: moneyline, full_game_total, team_total, f5_total, f5_winner

- Total: 333
- Fresh with bid/ask (<15min): 333 (100.0%)
- With any real coverage (<60min): 333 (100.0%)

_Note: `fresh_empty_book` = collector is running but market maker not yet active._
_This is expected behaviour hours before first pitch. Check again at T-60min._

## Coverage by Market Type

| Market Type | Total | Fresh | Fresh+Empty | Recent | Recent+Empty | Stale | StaleEmpty | Missing |
|-------------|-------|-------|-------------|--------|--------------|-------|------------|---------|
| f5_spread | 36 | 36 | 0 | 0 | 0 | 0 | 0 | 0 |
| f5_total* | 63 | 63 | 0 | 0 | 0 | 0 | 0 | 0 |
| f5_winner* | 27 | 27 | 0 | 0 | 0 | 0 | 0 | 0 |
| full_game_total* | 99 | 99 | 0 | 0 | 0 | 0 | 0 | 0 |
| moneyline* | 18 | 18 | 0 | 0 | 0 | 0 | 0 | 0 |
| player_hr | 112 | 0 | 0 | 0 | 0 | 0 | 0 | 112 |
| spread_run_line | 54 | 54 | 0 | 0 | 0 | 0 | 0 | 0 |
| team_total* | 126 | 126 | 0 | 0 | 0 | 0 | 0 | 0 |

_* = priority type used in EV overlay_

## Snapshot Timing

- Earliest snapshot today: 2026-06-25T16:02:00.094220+00:00
- Most recent snapshot: 2026-06-25T16:24:48.660155+00:00

## Stale / Missing Markets (112 total)

| Game | Market Type | Ticker | Label | Last Snap | Age (min) |
|------|-------------|--------|-------|-----------|-----------|
| ATH@SF | player_hr | KXMLBHR-26JUN251545ATHSF-ATHJWILSON5-1 | no_snapshots | never | n/a |
| ATH@SF | player_hr | KXMLBHR-26JUN251545ATHSF-ATHJWILSON5-2 | no_snapshots | never | n/a |
| ATH@SF | player_hr | KXMLBHR-26JUN251545ATHSF-ATHNKURTZ16-1 | no_snapshots | never | n/a |
| ATH@SF | player_hr | KXMLBHR-26JUN251545ATHSF-ATHNKURTZ16-2 | no_snapshots | never | n/a |
| ATH@SF | player_hr | KXMLBHR-26JUN251545ATHSF-SFRDEVERS16-1 | no_snapshots | never | n/a |
| ATH@SF | player_hr | KXMLBHR-26JUN251545ATHSF-SFRDEVERS16-2 | no_snapshots | never | n/a |
| AZ@STL | player_hr | KXMLBHR-26JUN251945AZSTL-AZCCARROLL7-1 | no_snapshots | never | n/a |
| AZ@STL | player_hr | KXMLBHR-26JUN251945AZSTL-AZCCARROLL7-2 | no_snapshots | never | n/a |
| AZ@STL | player_hr | KXMLBHR-26JUN251945AZSTL-AZGPERDOMO2-1 | no_snapshots | never | n/a |
| AZ@STL | player_hr | KXMLBHR-26JUN251945AZSTL-AZKMARTE4-1 | no_snapshots | never | n/a |
| AZ@STL | player_hr | KXMLBHR-26JUN251945AZSTL-AZKMARTE4-2 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-CHCABREGMAN3-1 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-CHCABREGMAN3-2 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-CHCPCROWARMSTRONG4-1 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-CHCPCROWARMSTRONG4-2 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-NYMFLINDOR12-1 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-NYMFLINDOR12-2 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-NYMJSOTO22-1 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-NYMJSOTO22-2 | no_snapshots | never | n/a |
| HOU@DET | player_hr | KXMLBHR-26JUN251840HOUDET-HOUCWALKER8-1 | no_snapshots | never | n/a |
| HOU@DET | player_hr | KXMLBHR-26JUN251840HOUDET-HOUCWALKER8-2 | no_snapshots | never | n/a |
| HOU@DET | player_hr | KXMLBHR-26JUN251840HOUDET-HOUJALTUVE27-1 | no_snapshots | never | n/a |
| HOU@DET | player_hr | KXMLBHR-26JUN251840HOUDET-HOUJALTUVE27-2 | no_snapshots | never | n/a |
| HOU@DET | player_hr | KXMLBHR-26JUN251840HOUDET-HOUYALVAREZ44-1 | no_snapshots | never | n/a |
| HOU@DET | player_hr | KXMLBHR-26JUN251840HOUDET-HOUYALVAREZ44-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCBWITT7-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCBWITT7-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCCJENSEN22-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCCJENSEN22-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCICOLLINS1-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCICOLLINS1-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCJCAGLIANONE14-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCJCAGLIANONE14-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCJROJAS40-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCJROJAS40-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCLTHOMAS15-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCLTHOMAS15-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCNLOFTIN12-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCNLOFTIN12-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCSMARTE0-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCSMARTE0-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCSPEREZ13-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCSPEREZ13-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCTTOLBERT2-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCTTOLBERT2-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBCMULLINS31-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBCMULLINS31-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBCSIMPSON14-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBHFEDUCCIA9-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBHFEDUCCIA9-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBJARANDA8-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBJARANDA8-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBJCAMINERO13-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBJCAMINERO13-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBRPALACIOS1-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBRPALACIOS1-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBTWALLS6-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBTWALLS6-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBVMESA25-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBVMESA25-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBYDIAZ2-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBYDIAZ2-2 | no_snapshots | never | n/a |
| NYY@BOS | player_hr | KXMLBHR-26JUN251910NYYBOS-NYYCBELLINGER35-1 | no_snapshots | never | n/a |
| NYY@BOS | player_hr | KXMLBHR-26JUN251910NYYBOS-NYYCBELLINGER35-2 | no_snapshots | never | n/a |
| PHI@WSH | player_hr | KXMLBHR-26JUN251845PHIWSH-PHIBHARPER3-1 | no_snapshots | never | n/a |
| PHI@WSH | player_hr | KXMLBHR-26JUN251845PHIWSH-PHIBHARPER3-2 | no_snapshots | never | n/a |
| PHI@WSH | player_hr | KXMLBHR-26JUN251845PHIWSH-PHIKSCHWARBER12-1 | no_snapshots | never | n/a |
| PHI@WSH | player_hr | KXMLBHR-26JUN251845PHIWSH-PHIKSCHWARBER12-2 | no_snapshots | never | n/a |
| PHI@WSH | player_hr | KXMLBHR-26JUN251845PHIWSH-PHIKSCHWARBER12-3 | no_snapshots | never | n/a |
| PHI@WSH | player_hr | KXMLBHR-26JUN251845PHIWSH-PHITTURNER7-1 | no_snapshots | never | n/a |
| PHI@WSH | player_hr | KXMLBHR-26JUN251845PHIWSH-PHITTURNER7-2 | no_snapshots | never | n/a |
| PHI@WSH | player_hr | KXMLBHR-26JUN251845PHIWSH-WSHCABRAMS5-1 | no_snapshots | never | n/a |
| PHI@WSH | player_hr | KXMLBHR-26JUN251845PHIWSH-WSHCABRAMS5-2 | no_snapshots | never | n/a |
| PHI@WSH | player_hr | KXMLBHR-26JUN251845PHIWSH-WSHJWOOD29-1 | no_snapshots | never | n/a |
| PHI@WSH | player_hr | KXMLBHR-26JUN251845PHIWSH-WSHJWOOD29-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITBLOWE5-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITBLOWE5-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITBREYNOLDS10-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITBREYNOLDS10-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITEVALDEZ85-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITEVALDEZ85-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITHDAVIS32-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITHDAVIS32-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITJMANGUM28-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITJTRIOLO19-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITNGONZALES3-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITROHEARN29-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITROHEARN29-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITTCALLIHAN37-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-PITTCALLIHAN37-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEACEMERSON85-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEACEMERSON85-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEACRALEIGH29-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEACRALEIGH29-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEACYOUNG2-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEACYOUNG2-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEAJCRAWFORD3-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEAJCRAWFORD3-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEAJNAYLOR12-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEAJNAYLOR12-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEAJRODRGUEZ44-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEAJRODRGUEZ44-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEALRALEY20-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEALRALEY20-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEAMGARVER77-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEAMGARVER77-2 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEARAROZARENA56-1 | no_snapshots | never | n/a |
| SEA@PIT | player_hr | KXMLBHR-26JUN251235SEAPIT-SEARAROZARENA56-2 | no_snapshots | never | n/a |
| TEX@TOR | player_hr | KXMLBHR-26JUN251907TEXTOR-TEXBNIMMO24-1 | no_snapshots | never | n/a |
| TEX@TOR | player_hr | KXMLBHR-26JUN251907TEXTOR-TEXBNIMMO24-2 | no_snapshots | never | n/a |
| TEX@TOR | player_hr | KXMLBHR-26JUN251907TEXTOR-TORVGUERRERO27-1 | no_snapshots | never | n/a |
| TEX@TOR | player_hr | KXMLBHR-26JUN251907TEXTOR-TORVGUERRERO27-2 | no_snapshots | never | n/a |

## Fresh Markets Sample (showing 15 of 423)

| Game | Type | Ticker | Bid | Ask | Spread | Age (min) |
|------|------|--------|-----|-----|--------|-----------|
| ATH@SF | full_game_total | KXMLBTOTAL-26JUN251545ATHSF-5 | 86 | 87 | 1 | 0.1 |
| HOU@DET | full_game_total | KXMLBTOTAL-26JUN251840HOUDET-12 | 27 | 28 | 1 | 0.1 |
| HOU@DET | full_game_total | KXMLBTOTAL-26JUN251840HOUDET-13 | 21 | 22 | 1 | 0.1 |
| KC@TB | team_total | KXMLBTEAMTOTAL-26JUN251210KCTB-KC2 | 71 | 86 | 15 | 0.1 |
| KC@TB | team_total | KXMLBTEAMTOTAL-26JUN251210KCTB-KC8 | 7 | 49 | 42 | 0.1 |
| CHC@NYM | team_total | KXMLBTEAMTOTAL-26JUN251910CHCNYM-NYM4 | 56 | 58 | 2 | 0.1 |
| KC@TB | f5_spread | KXMLBF5SPREAD-26JUN251210KCTB-KC2 | 7 | 15 | 8 | 0.2 |
| KC@TB | f5_spread | KXMLBF5SPREAD-26JUN251210KCTB-KC3 | 5 | 11 | 6 | 0.2 |
| KC@TB | f5_spread | KXMLBF5SPREAD-26JUN251210KCTB-TB2 | 60 | 63 | 3 | 0.2 |
| KC@TB | f5_spread | KXMLBF5SPREAD-26JUN251210KCTB-TB3 | 42 | 45 | 3 | 0.2 |
| SEA@PIT | f5_spread | KXMLBF5SPREAD-26JUN251235SEAPIT-PIT2 | 23 | 24 | 1 | 0.2 |
| SEA@PIT | f5_spread | KXMLBF5SPREAD-26JUN251235SEAPIT-PIT3 | 13 | 14 | 1 | 0.2 |
| SEA@PIT | f5_spread | KXMLBF5SPREAD-26JUN251235SEAPIT-SEA2 | 35 | 36 | 1 | 0.2 |
| SEA@PIT | f5_spread | KXMLBF5SPREAD-26JUN251235SEAPIT-SEA3 | 23 | 25 | 2 | 0.2 |
| ATH@SF | f5_spread | KXMLBF5SPREAD-26JUN251545ATHSF-ATH2 | 25 | 26 | 1 | 0.2 |

## Truly Stale / Missing Markets (112 total)
_(fresh_empty_book and recent_empty_book excluded — expected pre-game behaviour)_

| Game | Market Type | Ticker | Label | Last Snap | Age (min) |
|------|-------------|--------|-------|-----------|-----------|
| ATH@SF | player_hr | KXMLBHR-26JUN251545ATHSF-ATHJWILSON5-1 | no_snapshots | never | n/a |
| ATH@SF | player_hr | KXMLBHR-26JUN251545ATHSF-ATHJWILSON5-2 | no_snapshots | never | n/a |
| ATH@SF | player_hr | KXMLBHR-26JUN251545ATHSF-ATHNKURTZ16-1 | no_snapshots | never | n/a |
| ATH@SF | player_hr | KXMLBHR-26JUN251545ATHSF-ATHNKURTZ16-2 | no_snapshots | never | n/a |
| ATH@SF | player_hr | KXMLBHR-26JUN251545ATHSF-SFRDEVERS16-1 | no_snapshots | never | n/a |
| ATH@SF | player_hr | KXMLBHR-26JUN251545ATHSF-SFRDEVERS16-2 | no_snapshots | never | n/a |
| AZ@STL | player_hr | KXMLBHR-26JUN251945AZSTL-AZCCARROLL7-1 | no_snapshots | never | n/a |
| AZ@STL | player_hr | KXMLBHR-26JUN251945AZSTL-AZCCARROLL7-2 | no_snapshots | never | n/a |
| AZ@STL | player_hr | KXMLBHR-26JUN251945AZSTL-AZGPERDOMO2-1 | no_snapshots | never | n/a |
| AZ@STL | player_hr | KXMLBHR-26JUN251945AZSTL-AZKMARTE4-1 | no_snapshots | never | n/a |
| AZ@STL | player_hr | KXMLBHR-26JUN251945AZSTL-AZKMARTE4-2 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-CHCABREGMAN3-1 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-CHCABREGMAN3-2 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-CHCPCROWARMSTRONG4-1 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-CHCPCROWARMSTRONG4-2 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-NYMFLINDOR12-1 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-NYMFLINDOR12-2 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-NYMJSOTO22-1 | no_snapshots | never | n/a |
| CHC@NYM | player_hr | KXMLBHR-26JUN251910CHCNYM-NYMJSOTO22-2 | no_snapshots | never | n/a |
| HOU@DET | player_hr | KXMLBHR-26JUN251840HOUDET-HOUCWALKER8-1 | no_snapshots | never | n/a |
| HOU@DET | player_hr | KXMLBHR-26JUN251840HOUDET-HOUCWALKER8-2 | no_snapshots | never | n/a |
| HOU@DET | player_hr | KXMLBHR-26JUN251840HOUDET-HOUJALTUVE27-1 | no_snapshots | never | n/a |
| HOU@DET | player_hr | KXMLBHR-26JUN251840HOUDET-HOUJALTUVE27-2 | no_snapshots | never | n/a |
| HOU@DET | player_hr | KXMLBHR-26JUN251840HOUDET-HOUYALVAREZ44-1 | no_snapshots | never | n/a |
| HOU@DET | player_hr | KXMLBHR-26JUN251840HOUDET-HOUYALVAREZ44-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCBWITT7-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCBWITT7-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCCJENSEN22-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCCJENSEN22-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCICOLLINS1-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCICOLLINS1-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCJCAGLIANONE14-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCJCAGLIANONE14-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCJROJAS40-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCJROJAS40-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCLTHOMAS15-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCLTHOMAS15-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCNLOFTIN12-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCNLOFTIN12-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCSMARTE0-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCSMARTE0-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCSPEREZ13-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCSPEREZ13-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCTTOLBERT2-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-KCTTOLBERT2-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBCMULLINS31-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBCMULLINS31-2 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBCSIMPSON14-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBHFEDUCCIA9-1 | no_snapshots | never | n/a |
| KC@TB | player_hr | KXMLBHR-26JUN251210KCTB-TBHFEDUCCIA9-2 | no_snapshots | never | n/a |

## Not-Polled Market Types (112 markets)

Types: **player_hr**

These types are intentionally excluded from the collector. If a priority
type appears here, add it to `_DEFAULT_MARKET_TYPES` in `kalshi_orderbook_recorder.py`.

## Fresh Markets with Bid/Ask (showing 15 of 423)

| Game | Type | Ticker | Bid | Ask | Spread | Age (min) |
|------|------|--------|-----|-----|--------|-----------|
| ATH@SF | full_game_total | KXMLBTOTAL-26JUN251545ATHSF-5 | 86 | 87 | 1 | 0.1 |
| HOU@DET | full_game_total | KXMLBTOTAL-26JUN251840HOUDET-12 | 27 | 28 | 1 | 0.1 |
| HOU@DET | full_game_total | KXMLBTOTAL-26JUN251840HOUDET-13 | 21 | 22 | 1 | 0.1 |
| KC@TB | team_total | KXMLBTEAMTOTAL-26JUN251210KCTB-KC2 | 71 | 86 | 15 | 0.1 |
| KC@TB | team_total | KXMLBTEAMTOTAL-26JUN251210KCTB-KC8 | 7 | 49 | 42 | 0.1 |
| CHC@NYM | team_total | KXMLBTEAMTOTAL-26JUN251910CHCNYM-NYM4 | 56 | 58 | 2 | 0.1 |
| KC@TB | f5_spread | KXMLBF5SPREAD-26JUN251210KCTB-KC2 | 7 | 15 | 8 | 0.2 |
| KC@TB | f5_spread | KXMLBF5SPREAD-26JUN251210KCTB-KC3 | 5 | 11 | 6 | 0.2 |
| KC@TB | f5_spread | KXMLBF5SPREAD-26JUN251210KCTB-TB2 | 60 | 63 | 3 | 0.2 |
| KC@TB | f5_spread | KXMLBF5SPREAD-26JUN251210KCTB-TB3 | 42 | 45 | 3 | 0.2 |
| SEA@PIT | f5_spread | KXMLBF5SPREAD-26JUN251235SEAPIT-PIT2 | 23 | 24 | 1 | 0.2 |
| SEA@PIT | f5_spread | KXMLBF5SPREAD-26JUN251235SEAPIT-PIT3 | 13 | 14 | 1 | 0.2 |
| SEA@PIT | f5_spread | KXMLBF5SPREAD-26JUN251235SEAPIT-SEA2 | 35 | 36 | 1 | 0.2 |
| SEA@PIT | f5_spread | KXMLBF5SPREAD-26JUN251235SEAPIT-SEA3 | 23 | 25 | 2 | 0.2 |
| ATH@SF | f5_spread | KXMLBF5SPREAD-26JUN251545ATHSF-ATH2 | 25 | 26 | 1 | 0.2 |

## Collection Guidance

Coverage is partial. 423 fresh with bid/ask / 535 total (79.1%). 0 markets have fresh snapshots but empty books (no MM yet).

**Pregame window guidance:**
- First pitch as early as 16:05 UTC (12:05 ET)
- `fresh_empty_book` is normal until ~T-60min; MMs activate 30-60 min before pitch
- Target: ≥60% `fresh_with_bid_ask` at T-30min
- Ideal: collector running since 12:00 UTC (08:00 ET)
- If collector gap found: check 'MLB2 Orderbook Recorder' window, restart if needed

**Recommended EV overlay timing:**
- Run EV overlay 60-90 minutes before each game's first pitch
- Re-run after each game block (afternoon / evening / late night)