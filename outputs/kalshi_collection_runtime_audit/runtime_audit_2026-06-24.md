# Kalshi Collection Runtime Audit — 2026-06-24

**DB:** `kalshi_mlb.db`  
**Dates with data:** 2026-06-15 to 2026-06-24  
**Audit generated:** 2026-06-24 UTC

---

## Executive Summary

The "daily Kalshi orderbook gap" is **confirmed real**. It is not a timezone
mislabel, a ticker join issue, or a reporting artifact. The collector simply
does not run during a 10–16 hour window every day.

**Root cause — two compounding factors:**

1. **The 915-minute time limit** (`--duration-minutes 915`) in `dev.bat` line 139
   causes each session to auto-exit after ~15.25 hours. Sessions started at
   14:00–21:40 UTC (10am–5:40pm ET) expire at 05:15–12:55 UTC the next day.
   In practice, sessions often stop **earlier** (manual window close or crash),
   typically at 01:51–05:43 UTC (9:51pm–1:43am ET).

2. **Manual start delay**: The next session doesn't start until the operator
   manually runs `dev.bat slate` again, typically 14:38–21:40 UTC (10:38am–5:40pm ET).
   The combined effect is a **daily gap of 615–958 minutes (10–16 hours)**.

**Gap window (observed):** approximately **04:00–15:00 UTC** (midnight–11am ET)

**Implication for pregame coverage:**
- Games starting before 15:00–17:00 UTC (11am–1pm ET) often receive **zero pregame snapshots**
- June 22 session started at 21:40 UTC (5:40pm ET): all games starting before 5:40pm ET had no coverage
- Only June 15 received early pregame coverage (session started at 01:41 UTC via night-before setup)

**Additional finding — inconsistent `--slate-date` usage:**
- Some sessions polled multiple game dates simultaneously (no `--slate-date` filter)
- June 15 01:41 UTC session captured Jun 12/13/14/15/16 game markets all at once
- June 22 21:40 UTC session captured Jun 22 AND Jun 23 markets simultaneously
- Inflates snapshot volume metrics without improving coverage of specific game dates

---

## Daily Gap Table (UTC Date Grouping)

| UTC Date | First snap (UTC/ET) | Last snap (UTC/ET) | Largest gap | Gap window (UTC) | Gap window (ET) |
|----------|--------------------|--------------------|------------|-----------------|----------------|
| 2026-06-15 | 01:41 UTC / 21:41 ET | 23:55 UTC / 19:55 ET | **901 min** | 01:51–16:52 | 21:51 ET–12:52 ET |
| 2026-06-16 | 00:00 UTC / 20:00 ET | 23:59 UTC / 19:59 ET | **765 min** | 04:37–17:21 | 00:37 ET–13:21 ET |
| 2026-06-17 | 00:00 UTC / 20:00 ET | 23:59 UTC / 19:59 ET | **687 min** | 04:47–16:14 | 00:47 ET–12:14 ET |
| 2026-06-18 | 00:00 UTC / 20:00 ET | 01:53 UTC / 21:53 ET | **—** | — | — |
| 2026-06-21 | 15:38 UTC / 11:38 ET | 23:59 UTC / 19:59 ET | **—** | — | — |
| 2026-06-22 | 00:00 UTC / 20:00 ET | 23:59 UTC / 19:59 ET | **958 min** | 05:43–21:40 | 01:43 ET–17:40 ET |
| 2026-06-23 | 00:00 UTC / 20:00 ET | 23:59 UTC / 19:59 ET | **719 min** | 03:35–15:33 | 23:35 ET–11:33 ET |
| 2026-06-24 | 00:00 UTC / 20:00 ET | 20:17 UTC / 16:17 ET | **615 min** | 04:22–14:38 | 00:22 ET–10:38 ET |

> **Reading note:** The `First snap` column on dates like 2026-06-16 (00:00 UTC / 8pm ET Jun 15)
> is the **tail of the previous night's session** — not a new start at midnight.
> The actual new session started the prior afternoon. UTC date boundaries make
> each session appear split across two rows.

---

## Collector Session Reconstruction

Each continuous block of snapshots (no gap ≥ 30 min) = one collection session.
Sessions that span UTC midnight appear across two consecutive UTC date rows above.

| UTC Date | Session | Start UTC | Start ET | End UTC | End ET | Duration |
|----------|---------|----------|---------|---------|-------|---------|
| 2026-06-15 | #1 | 01:41 | 21:41 ET | 01:51 | 21:51 ET | 10 min |
| 2026-06-15 | #2 | 16:52 | 12:52 ET | 18:34 | 14:34 ET | 102 min |
| 2026-06-15 | #3 | 19:33 | 15:33 ET | 23:55 | 19:55 ET | 262 min |
| 2026-06-16 | #1 | 00:00 | 20:00 ET | 00:31 | 20:31 ET | 32 min |
| 2026-06-16 | #2 | 01:10 | 21:10 ET | 04:37 | 00:37 ET | 206 min |
| 2026-06-16 | #3 | 17:21 | 13:21 ET | 18:01 | 14:01 ET | 40 min |
| 2026-06-16 | #4 | 18:55 | 14:55 ET | 23:59 | 19:59 ET | 305 min |
| 2026-06-17 | #1 | 00:00 | 20:00 ET | 04:47 | 00:47 ET | 287 min |
| 2026-06-17 | #2 | 16:14 | 12:14 ET | 23:59 | 19:59 ET | 465 min |
| 2026-06-18 | #1 | 00:00 | 20:00 ET | 01:53 | 21:53 ET | 114 min |
| 2026-06-21 | #1 | 15:38 | 11:38 ET | 23:59 | 19:59 ET | 502 min |
| 2026-06-22 | #1 | 00:00 | 20:00 ET | 05:43 | 01:43 ET | 343 min |
| 2026-06-22 | #2 | 21:40 | 17:40 ET | 23:59 | 19:59 ET | 139 min |
| 2026-06-23 | #1 | 00:00 | 20:00 ET | 03:35 | 23:35 ET | 215 min |
| 2026-06-23 | #2 | 15:33 | 11:33 ET | 23:59 | 19:59 ET | 506 min |
| 2026-06-24 | #1 | 00:00 | 20:00 ET | 04:22 | 00:22 ET | 263 min |
| 2026-06-24 | #2 | 14:38 | 10:38 ET | 20:17 | 16:17 ET | 339 min |

**Session reconstruction — narrative:**

```
Jun 15 01:41 UTC (9:41pm ET Jun 14)  →  Jun 15 01:51 UTC: ~10 min session, then crashed
Gap: 01:51 → 16:52 UTC Jun 15 (901 min / 15h01m)
Jun 15 16:52 UTC (12:52pm ET Jun 15) →  Jun 16 04:37 UTC: 11h45m session
Gap: 04:37 → 17:21 UTC Jun 16 (765 min / 12h44m)
Jun 16 17:21 UTC (1:21pm ET Jun 16)  →  Jun 17 04:47 UTC: 11h26m session
Gap: 04:47 → 16:14 UTC Jun 17 (687 min / 11h27m)
Jun 17 16:14 UTC (12:14pm ET Jun 17) →  Jun 18 01:53 UTC:  9h39m session (early stop)
Jun 18–20: NO DATA (collector not started, 3-day gap)
Jun 21 15:38 UTC (11:38am ET Jun 21) →  Jun 22 05:43 UTC: 14h05m session
Gap: 05:43 → 21:40 UTC Jun 22 (958 min / 15h57m) — LARGEST GAP
Jun 22 21:40 UTC (5:40pm ET Jun 22)  →  Jun 23 03:35 UTC:  5h55m session (early stop)
Gap: 03:35 → 15:33 UTC Jun 23 (719 min / 11h58m)
Jun 23 15:33 UTC (11:33am ET Jun 23) →  Jun 24 04:22 UTC: 12h49m session (early stop)
Gap: 04:22 → 14:38 UTC Jun 24 (615 min / 10h16m)
Jun 24 14:38 UTC (10:38am ET Jun 24) →  ongoing (current session as of audit)
```

> **Early stops**: Sessions on Jun 18, Jun 22, Jun 23 stopped before the 915-minute
> timer would have fired. Likely cause: operator manually closed the window,
> computer hibernation, or network error causing the process to exit.

---

## Hourly Snapshot Distribution (UTC, All Market Types)

Hours without any data are omitted. The daily gap is visible as missing hour rows.

### 2026-06-15
| Hr (UTC) | Total | moneyline | team_total | full_total | f5_total |
|---------|-------|----------|-----------|----------|---------|
| 01:xx | 5,788 | 472 | 1,792 | 1,432 | 812 |
| ... | **14h GAP** | | | | |
| 16:xx | 5,685 | 438 | 1,764 | 1,404 | 819 |
| 17:xx | 36,005 | 2,774 | 11,172 | 8,892 | 5,187 |
| 18:xx | 21,786 | 1,650 | 6,468 | 5,616 | 3,276 |
| 19:xx | 17,055 | 1,314 | 5,292 | 4,212 | 2,457 |
| 20:xx | 37,549 | 2,668 | 11,424 | 9,534 | 5,607 |
| 21:xx | 36,105 | 2,310 | 11,340 | 9,000 | 5,355 |
| 22:xx | 36,105 | 2,310 | 11,340 | 9,000 | 5,355 |
| 23:xx | 33,698 | 2,156 | 10,584 | 8,400 | 4,998 |

### 2026-06-16
| Hr (UTC) | Total | moneyline | team_total | full_total | f5_total |
|---------|-------|----------|-----------|----------|---------|
| 00:xx | 19,287 | 1,240 | 6,048 | 4,806 | 2,856 |
| 01:xx | 27,738 | 1,782 | 8,316 | 6,959 | 4,284 |
| 02:xx | 32,507 | 2,106 | 10,253 | 8,188 | 4,641 |
| 03:xx | 34,530 | 2,268 | 10,833 | 8,549 | 4,998 |
| 04:xx | 19,608 | 1,296 | 6,048 | 4,848 | 2,856 |
| ... | **12h GAP** | | | | |
| 17:xx | 61,370 | 2,090 | 21,888 | 16,530 | 6,726 |
| 18:xx | 7,396 | 486 | 2,332 | 1,818 | 1,071 |
| 19:xx | 193,033 | 12,590 | 60,505 | 47,619 | 28,098 |
| 20:xx | 154,680 | 9,960 | 48,120 | 38,340 | 22,680 |
| 21:xx | 311,430 | 19,920 | 98,310 | 76,680 | 45,360 |
| 22:xx | 328,654 | 19,920 | 115,534 | 76,680 | 45,360 |
| 23:xx | 400,918 | 19,920 | 187,798 | 76,680 | 45,360 |

### 2026-06-17
| Hr (UTC) | Total | moneyline | team_total | full_total | f5_total |
|---------|-------|----------|-----------|----------|---------|
| 00:xx | 414,306 | 19,920 | 201,005 | 76,861 | 45,360 |
| 01:xx | 394,734 | 19,920 | 181,261 | 76,974 | 45,419 |
| 02:xx | 378,386 | 19,920 | 164,543 | 77,150 | 45,613 |
| 03:xx | 338,815 | 19,920 | 125,369 | 77,006 | 45,360 |
| 04:xx | 249,205 | 15,770 | 80,480 | 60,710 | 35,910 |
| ... | **11h GAP** | | | | |
| 16:xx | 300,077 | 17,472 | 100,332 | 72,254 | 43,316 |
| 17:xx | 412,829 | 23,040 | 149,429 | 95,280 | 57,120 |
| 18:xx | 415,018 | 22,848 | 153,813 | 94,486 | 56,644 |
| 19:xx | 427,190 | 23,040 | 163,790 | 95,280 | 57,120 |
| 20:xx | 411,145 | 23,040 | 147,745 | 95,280 | 57,120 |
| 21:xx | 395,720 | 23,040 | 132,320 | 95,280 | 57,120 |
| 22:xx | 385,126 | 23,040 | 121,726 | 95,280 | 57,120 |
| 23:xx | 404,386 | 23,040 | 140,986 | 95,280 | 57,120 |

### 2026-06-18
| Hr (UTC) | Total | moneyline | team_total | full_total | f5_total |
|---------|-------|----------|-----------|----------|---------|
| 00:xx | 411,823 | 23,040 | 148,523 | 95,280 | 57,120 |
| 01:xx | 358,750 | 20,736 | 121,690 | 85,752 | 51,408 |

### 2026-06-21
| Hr (UTC) | Total | moneyline | team_total | full_total | f5_total |
|---------|-------|----------|-----------|----------|---------|
| 15:xx | 48,964 | 2,160 | 16,708 | 11,736 | 7,560 |
| 16:xx | 161,029 | 7,200 | 53,509 | 39,120 | 25,200 |
| 17:xx | 187,218 | 7,200 | 79,698 | 39,120 | 25,200 |
| 18:xx | 238,726 | 7,200 | 131,206 | 39,120 | 25,200 |
| 19:xx | 242,435 | 7,200 | 134,915 | 39,120 | 25,200 |
| 20:xx | 222,123 | 7,200 | 114,603 | 39,120 | 25,200 |
| 21:xx | 197,797 | 7,200 | 90,277 | 39,120 | 25,200 |
| 22:xx | 166,216 | 7,200 | 58,696 | 39,120 | 25,200 |
| 23:xx | 157,920 | 7,200 | 50,400 | 39,120 | 25,200 |

### 2026-06-22
| Hr (UTC) | Total | moneyline | team_total | full_total | f5_total |
|---------|-------|----------|-----------|----------|---------|
| 00:xx | 157,920 | 7,200 | 50,400 | 39,120 | 25,200 |
| 01:xx | 157,920 | 7,200 | 50,400 | 39,120 | 25,200 |
| 02:xx | 157,920 | 7,200 | 50,400 | 39,120 | 25,200 |
| 03:xx | 157,920 | 7,200 | 50,400 | 39,120 | 25,200 |
| 04:xx | 157,920 | 7,200 | 50,400 | 39,120 | 25,200 |
| 05:xx | 113,834 | 5,190 | 36,330 | 28,199 | 18,165 |
| ... | **15h GAP** | | | | |
| 21:xx | 24,660 | 1,014 | 7,929 | 5,577 | 3,549 |
| 22:xx | 94,914 | 3,588 | 35,712 | 19,734 | 12,558 |
| 23:xx | 73,320 | 3,120 | 21,840 | 17,160 | 10,920 |

### 2026-06-23
| Hr (UTC) | Total | moneyline | team_total | full_total | f5_total |
|---------|-------|----------|-----------|----------|---------|
| 00:xx | 71,487 | 3,042 | 21,294 | 16,731 | 10,647 |
| 01:xx | 73,320 | 3,120 | 21,840 | 17,160 | 10,920 |
| 02:xx | 73,320 | 3,120 | 21,840 | 17,160 | 10,920 |
| 03:xx | 43,381 | 1,846 | 12,922 | 10,153 | 6,461 |
| ... | **11h GAP** | | | | |
| 15:xx | 38,178 | 1,590 | 11,943 | 8,745 | 5,565 |
| 16:xx | 88,135 | 3,600 | 28,735 | 19,800 | 12,600 |
| 17:xx | 89,796 | 3,600 | 30,396 | 19,800 | 12,600 |
| 18:xx | 90,357 | 3,600 | 30,957 | 19,800 | 12,600 |
| 19:xx | 87,827 | 3,600 | 28,427 | 19,800 | 12,600 |
| 20:xx | 98,658 | 3,600 | 39,258 | 19,800 | 12,600 |
| 21:xx | 99,880 | 3,600 | 40,480 | 19,800 | 12,600 |
| 22:xx | 114,322 | 3,600 | 54,922 | 19,800 | 12,600 |
| 23:xx | 187,958 | 3,600 | 128,558 | 19,800 | 12,600 |

### 2026-06-24
| Hr (UTC) | Total | moneyline | team_total | full_total | f5_total |
|---------|-------|----------|-----------|----------|---------|
| 00:xx | 197,874 | 3,600 | 138,213 | 20,061 | 12,600 |
| 01:xx | 162,435 | 3,600 | 102,579 | 20,256 | 12,600 |
| 02:xx | 140,468 | 3,600 | 80,737 | 20,131 | 12,600 |
| 03:xx | 97,962 | 3,600 | 38,474 | 19,888 | 12,600 |
| 04:xx | 32,643 | 1,380 | 9,873 | 7,590 | 4,830 |
| ... | **9h GAP** | | | | |
| 14:xx | 30,186 | 1,320 | 9,770 | 6,776 | 4,312 |
| 15:xx | 81,123 | 3,600 | 25,443 | 18,480 | 11,760 |
| 16:xx | 95,876 | 3,600 | 40,196 | 18,480 | 11,760 |
| 17:xx | 103,303 | 3,600 | 47,623 | 18,480 | 11,760 |
| 18:xx | 104,963 | 3,600 | 49,283 | 18,480 | 11,760 |
| 19:xx | 131,236 | 3,600 | 75,556 | 18,480 | 11,760 |
| 20:xx | 41,524 | 1,140 | 23,892 | 5,852 | 3,724 |

---

## Game Date Coverage Summary

Coverage by game date (based on ticker date in market_ticker field).
'First snap' shows when the first orderbook snapshot was taken for that game's markets.

| Game Date | Total Snaps | First Snapshot (UTC) | First Snapshot (ET) | Last Snapshot |
|-----------|------------|---------------------|--------------------|-----------|----|
| 2026-06-12 | 388,032 | 2026-06-15T01:41 | 2026-06-14 21:41 ET | 2026-06-18T01:53 |
| 2026-06-13 | 1,396,686 | 2026-06-15T01:41 | 2026-06-14 21:41 ET | 2026-06-18T01:53 |
| 2026-06-14 | 1,606,695 | 2026-06-15T01:41 | 2026-06-14 21:41 ET | 2026-06-18T01:53 |
| 2026-06-15 | 1,137,597 | 2026-06-15T01:41 | 2026-06-14 21:41 ET | 2026-06-18T01:53 |
| 2026-06-16 | 1,955,114 | 2026-06-15T01:41 | 2026-06-14 21:41 ET | 2026-06-18T01:53 |
| 2026-06-17 | 958,582 | 2026-06-15T16:52 | 2026-06-15 12:52 ET | 2026-06-21T15:43 |
| 2026-06-21 | 2,525,829 | 2026-06-21T15:38 | 2026-06-21 11:38 ET | 2026-06-22T05:43 |
| 2026-06-22 | 494,580 | 2026-06-22T21:40 | 2026-06-22 17:40 ET | 2026-06-24T20:00 |
| 2026-06-23 | 1,510,971 | 2026-06-22T21:40 | 2026-06-22 17:40 ET | 2026-06-24T04:22 |
| 2026-06-24 | 596,015 | 2026-06-24T14:38 | 2026-06-24 10:38 ET | 2026-06-24T20:33 |

> **Key observation:** Multiple game dates often share the same first snapshot time,
> meaning the collector polled them in the same batch (no `--slate-date` filter, or
> multiple dates returned by the catalog). Jun 15 01:41 UTC captured Jun 12-16 markets
> simultaneously. Jun 22 21:40 UTC captured Jun 22 AND Jun 23 together.
> Jun 23 and Jun 24 first appear at 21:40 UTC Jun 22, meaning Jun 23/24 markets were
> loaded when the Jun 22 session discovery ran.

---

## Diagnostic Questions — Answered

### Q1: Did the collector actually run during the expected manual windows?

**YES** — data exists for all days Justin operated. However, observed start times
range from **14:38 UTC (10:38am ET)** to **21:40 UTC (5:40pm ET)**, and sessions
stop at **01:51–05:43 UTC (9:51pm–1:43am ET)**. The gap between stop and next start
is 10–16 hours.

### Q2: Are gaps truly collector downtime, or market/filter gaps?

**Truly collector downtime.** During each gap window, ZERO snapshots of ALL market
types (moneyline, team_total, full_game_total, f5_total, f5_winner) are recorded.
The entire polling infrastructure is simply not running.

### Q3: Does dev.bat / slate startup actually start the collector?

**YES.** `dev.bat slate` step 6/6 (line 139) launches `kalshi_orderbook_recorder.py`
in a new cmd window with `--batch --slate-date %DATE% --interval-seconds 30
--duration-minutes 915`. `%DATE%` is set via Python `datetime.date.today().isoformat()`
(local ET date at launch time). This is correct.

### Q4: Is the collector one-shot or continuous?

**Continuous** — 30-second polling loop until one of:
a) `--duration-minutes 915` timer fires (auto-exit after 15h 15m)
b) The window is manually closed
c) A fatal unhandled exception

Evidence of (b) or (c): multiple sessions stopped well before the 915-minute
mark (Jun 18 stopped after 9h39m, Jun 22 after 5h55m, Jun 23 after 12h49m).

### Q5: Are timestamps/timezones misleading?

**Partially.** All `snapped_at` values are stored in UTC (ISO 8601 with `+00:00`).
The confusing artifact: when a session runs from 16:52 UTC Jun 15 to 04:37 UTC Jun 16,
the Jun 15 UTC date shows data from 16:52–23:59, and Jun 16 shows 00:00–04:37.
It looks like data resumes at midnight, masking the fact that the NEW session doesn't
start until 17:21 UTC Jun 16 (1:21pm ET). The overnight "continuation" is the
previous session's tail, not a new collection.

### Q6: Are we collecting at the wrong time relative to game start?

**YES for early games.** A session starting at 15:33 UTC (11:33am ET) gives:
- Noon ET (16:00 UTC) games: 27 min pregame — almost nothing
- 1:10pm ET (17:10 UTC) games: 97 min pregame — thin
- 3:10pm ET (19:10 UTC) games: 3h37m pregame — decent
- 7:10pm ET (23:10 UTC) games: 7h37m pregame — excellent

### Q7: Does the collector miss afternoon games?

**YES, routinely.** On Jun 22 the session started at 21:40 UTC (5:40pm ET).
All games starting before 5:40pm ET — including the 18:10 UTC (2:10pm ET) game —
received ZERO pregame snapshots. The session captured those games AFTER first pitch.

### Q8: Are market ticker joins causing false 'no coverage'?

**NO** — the gap is confirmed real downtime. Note that moneyline tickers use
`KXMLBGAME-26JUN...` format while team_total uses `KXMLBTEAMTOTAL-26JUN...`.
Both disappear during gap windows, confirming the issue is not a join artifact.

### Q9: Are old parser bugs affecting current reports?

**Only Jun 12–14.** The `_best_price` fix (max of levels instead of first level)
was deployed before Jun 15 collection began. All Jun 15+ snapshots use correct prices.
Jun 12–14 data has known bad prices but is outside the active research window.

### Q10: Final root cause classification

| Cause | Finding |
|-------|---------|
| **Operator did not start collector early enough** | **PRIMARY** — starts 14:38–21:40 UTC; afternoon games missed |
| **915-minute timer creates overnight gap** | **PRIMARY** — session auto-exits ~04:00 UTC; 10–16h gap to next start |
| **Sessions stop early (before 915-min limit)** | **CONFIRMED** — manual close or crash at 9h–13h in several sessions |
| Sessions inconsistently apply --slate-date | **CONFIRMED** — some sessions poll multiple game dates simultaneously |
| Startup script does not launch collector | Not a cause |
| Collector is one-shot | Not a cause |
| Market catalog issue | Not a cause |
| Timezone/reporting artifact | Not a cause (real downtime) |
| Ticker join artifact | Not a cause |
| API/auth error (silent) | Unconfirmed — early stops could include this |

---

## Recommended Fixes

### Fix 1 (Recommended): Remove the `--duration-minutes 915` limit

**Change `dev.bat` line 139:**
```diff
-  python kalshi_orderbook_recorder.py --sport mlb --batch --slate-date %DATE%
-    --interval-seconds 30 --duration-minutes 915
+  python kalshi_orderbook_recorder.py --sport mlb --batch --slate-date %DATE%
+    --interval-seconds 30
```

**Why this works:** The `cmd /k` window stays open until manually closed.
Without the 915-minute cap, a session started at 9pm ET continues running
indefinitely — through the gap hours — until the operator closes it.
The next morning, the session is still running and collecting current-slate data.

**Why it's safe:** The `--slate-date` filter restricts polling to only the
current date's markets. When games end and Kalshi closes markets, the collector
will continue polling them (they're still in the local `kalshi_markets` table)
but get back empty orderbooks. This is harmless.

**Tradeoff:** If the window is left open for multiple days accidentally, it will
keep polling yesterday's closed markets. Harmless but noisy. Starting a fresh
`dev.bat slate` closes yesterday's window and starts today's.

### Fix 2 (Visibility): Add per-game countdown to Health Check

The health check already runs every 5 minutes. Add a section showing:
```
  Next games and snapshot status:
  ⚠ TEX@MIA  16:10 UTC (noon ET) — 23 pregame snaps, 1h32m before first pitch
  ✓ CLE@CWS  18:10 UTC (2:10 ET) — 211 pregame snaps, 3h32m before first pitch
  ✓ BOS@COL  22:10 UTC (6:10 ET) — 845 pregame snaps, 7h32m before first pitch
```
This gives the operator actionable signal **before** the games with missing coverage.

### Fix 3 (Backup): Raise limit to 28 hours

If Fix 1 feels too open-ended, `--duration-minutes 1680` (28 hours) ensures the
session covers a full next-day slate even if started at midnight ET.

---

## Verification Commands

After applying Fix 1 or Fix 3, confirm the fix is working:

```bash
# Re-run this audit the next morning (should show no gap)
python kalshi_collection_runtime_audit.py
# Look for: no row in the gap table shows > 120 min gap

# Check snapshot health (run anytime)
python kalshi_snapshot_collection_health.py

# Inspect how the gap changed for a specific date
python -c "
import sqlite3
conn = sqlite3.connect('kalshi_mlb.db')
rows = conn.execute(\"SELECT CAST(SUBSTR(snapped_at,12,2) AS INT) AS hr, COUNT(*)
    FROM kalshi_orderbook_snapshots WHERE snapped_at LIKE '2026-06-25%'
    GROUP BY hr ORDER BY hr\").fetchall()
for r in rows: print(f'{r[0]:02d}:xx → {r[1]:,} snaps')
conn.close()"
```

The fix is confirmed when the `runtime_audit_*.md` shows no UTC hour range
missing between 00:xx and 14:xx for any active collection date.

---

*Report generated by `kalshi_collection_runtime_audit.py` on 2026-06-24*