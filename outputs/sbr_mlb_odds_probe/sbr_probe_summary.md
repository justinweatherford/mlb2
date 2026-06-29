# SBR MLB Odds Feasibility Probe
Generated: 2026-06-23 01:47 | Read-only research. No trades.

---

## Quick Answers

1. **SBR current MLB odds (2026):** YES -- __NEXT_DATA__ embedded JSON, no JS rendering needed
2. **SBR historical dates (pre-2026):** YES -- data goes back to at least 2021
3. **April 25, 2026 UI limit:** UI-only limit -- data is accessible via direct URL for any date
4. **Data format:** Embedded JSON (__NEXT_DATA__) in raw HTML -- no Playwright required
5. **Moneyline parseable:** YES -- 652 rows from 7 date(s)
6. **DB matchability:** 79/91 games matched (87%)
7. **Suitable for ML Core v1 validation:** YES -- full team names match DB, full historical coverage available
8. **Playwright needed:** No -- raw HTML contains all data

---

## Data Structure Confirmed

```
URL: https://www.sportsbookreview.com/betting-odds/mlb-baseball/money-line/full-game/?date=YYYY-MM-DD
__NEXT_DATA__.props.pageProps.oddsTables[0].oddsTableModel
  .sportsbooks[]           -- list of books: BetMGM, FanDuel, Caesars, bet365, DraftKings, Fanatics
  .gameRows[i]
    .gameView
      .awayTeam.fullName   -- full team name, matches mlb_games
      .homeTeam.fullName
      .startDate           -- ISO timestamp
      .awayStarter / .homeStarter.lastName
    .oddsViews[j]
      .sportsbook          -- machine name (e.g. 'betmgm')
      .currentLine.awayOdds / .homeOdds  -- American odds (e.g. -130, 105)
      .openingLine.awayOdds / .homeOdds  -- opening line
```

---

## URL Probe Results

Label                                            Status  Games Conf           ND_size
-------------------------------------------------------------------------------------
main/2026-06-22                                     200     12 likely_full     204653
main/2026-04-25                                     200     14 likely_full     217631
main/2026-04-24                                     200     14 likely_full     217384
main/2025-07-10                                     200     10 likely_full     187001
main/2024-07-10                                     200     17 likely_full     228308
main/2023-07-15                                     200     18 likely_full     234333
main/2021-04-04                                     200     12 likely_full     190322
money-line/full-game/2026-06-22                     200     12 likely_full     204796
pointspread/full-game/2026-06-22                    200     12 likely_full     205276
totals/full-game/2026-06-22                         200     12 likely_full     204744
pointspread/first-half/2026-06-22                   500      0 none                 0
totals/first-half/2026-06-22                        500      0 none                 0

---

## Moneyline Sample

Parsed 652 odds rows across 7 date(s): 2021-04-04, 2023-07-15, 2024-07-10, 2025-07-10, 2026-04-24, 2026-04-25, 2026-06-22

Date         Away                      Home                      Book            Away ML  Home ML  Away Open  Home Open
-------------------------------------------------------------------------------------------------------------------
2026-06-22   New York Yankees          Detroit Tigers            BetMGM             -130     +105       -135       +110
2026-06-22   New York Yankees          Detroit Tigers            FanDuel            -126     +108       -124       +106
2026-06-22   New York Yankees          Detroit Tigers            Caesars            -130     +110       -130       +110
2026-06-22   New York Yankees          Detroit Tigers            bet365             -132     +101       -139       +105
2026-06-22   New York Yankees          Detroit Tigers            DraftKings         -125     +104       -126       +104
2026-06-22   New York Yankees          Detroit Tigers            Fanatics Spor      -125     +105       -140       +115
2026-06-22   Kansas City Royals        Tampa Bay Rays            BetMGM             +155     -190       +145       -180
2026-06-22   Kansas City Royals        Tampa Bay Rays            FanDuel            +154     -184       +152       -180
2026-06-22   Kansas City Royals        Tampa Bay Rays            Caesars            +158     -190       +140       -165
2026-06-22   Kansas City Royals        Tampa Bay Rays            bet365             +155     -208       +135       -179
2026-06-22   Kansas City Royals        Tampa Bay Rays            DraftKings         +158     -193       +141       -171
2026-06-22   Kansas City Royals        Tampa Bay Rays            Fanatics Spor      +155     -190       +130       -155
2026-06-22   Texas Rangers             Miami Marlins             BetMGM             +110     -135       +105       -125
2026-06-22   Texas Rangers             Miami Marlins             FanDuel            +110     -130       +106       -124
2026-06-22   Texas Rangers             Miami Marlins             Caesars            +115     -135       +100       -120
2026-06-22   Texas Rangers             Miami Marlins             bet365             +110     -143       -104       -127
2026-06-22   Texas Rangers             Miami Marlins             DraftKings         +113     -136       +102       -122
2026-06-22   Texas Rangers             Miami Marlins             Fanatics Spor      +110     -130       +105       -125
2026-06-22   Philadelphia Phillies     Washington Nationals      BetMGM             +105     -125     -10000     -10000
2026-06-22   Philadelphia Phillies     Washington Nationals      FanDuel            +106     -124       -102       -116
2026-06-22   Philadelphia Phillies     Washington Nationals      Caesars            +105     -125       -105       -115
2026-06-22   Philadelphia Phillies     Washington Nationals      bet365             +101     -132       -110       -122
2026-06-22   Philadelphia Phillies     Washington Nationals      DraftKings         +104     -126       -106       -114
2026-06-22   Philadelphia Phillies     Washington Nationals      Fanatics Spor      +105     -125       -105       -115
2026-06-22   Houston Astros            Toronto Blue Jays         BetMGM             +118     -145       +110       -135
2026-06-22   Houston Astros            Toronto Blue Jays         FanDuel            +112     -132       -102       -116
2026-06-22   Houston Astros            Toronto Blue Jays         Caesars            +118     -140       +110       -130
2026-06-22   Houston Astros            Toronto Blue Jays         bet365             +115     -149       +105       -139
2026-06-22   Houston Astros            Toronto Blue Jays         DraftKings         +113     -136       +109       -131
2026-06-22   Houston Astros            Toronto Blue Jays         Fanatics Spor      +115     -140       +105       -125
2026-06-22   Milwaukee Brewers         Cincinnati Reds           BetMGM             -155     +125       -175       +145
2026-06-22   Milwaukee Brewers         Cincinnati Reds           FanDuel            -152     +128       -162       +136
2026-06-22   Milwaukee Brewers         Cincinnati Reds           Caesars            -155     +130       -178       +150
2026-06-22   Milwaukee Brewers         Cincinnati Reds           bet365             -167     +125       -185       +140
2026-06-22   Milwaukee Brewers         Cincinnati Reds           DraftKings         -156     +129       -175       +144
2026-06-22   Milwaukee Brewers         Cincinnati Reds           Fanatics Spor      -150     +125       -195       +160
2026-06-22   Los Angeles Dodgers       Minnesota Twins           BetMGM             -155     +125       -155       +125
2026-06-22   Los Angeles Dodgers       Minnesota Twins           FanDuel            -148     +126       -158       +134
2026-06-22   Los Angeles Dodgers       Minnesota Twins           Caesars            -155     +130       -155       +130
2026-06-22   Los Angeles Dodgers       Minnesota Twins           bet365             -167     +125       -167       +125

---

## Matchability

- Games parsed (unique game/date combos): 91
- Games matched to mlb_games DB: 79
- Unmatched SBR rows: 12
- Unmatched DB rows: 85
- Fuzzy matches (team name mapping differences):
  - 2026-04-25 Athletics Athletics@Texas Rangers -> db Athletics@Texas Rangers (fuzzy)
  - 2026-04-24 Athletics Athletics@Texas Rangers -> db Athletics@Texas Rangers (fuzzy)
  - 2025-07-10 Atlanta Braves@Athletics Athletics -> db Atlanta Braves@Athletics (fuzzy)
- SBR games not in DB:
  - 2021-04-04 Toronto Blue Jays@New York Yankees
  - 2021-04-04 Atlanta Braves@Philadelphia Phillies
  - 2021-04-04 Baltimore Orioles@Boston Red Sox
  - 2021-04-04 Cleveland Guardians@Detroit Tigers
  - 2021-04-04 St. Louis Cardinals@Cincinnati Reds
  - 2021-04-04 Texas Rangers@Kansas City Royals
  - 2021-04-04 Minnesota Twins@Milwaukee Brewers
  - 2021-04-04 Pittsburgh Pirates@Chicago Cubs
  - 2021-04-04 Los Angeles Dodgers@Colorado Rockies
  - 2021-04-04 Houston Astros@Oakland Athletics

---

## Next Steps

- [ ] Build production `sbr_mlb_odds_fetcher.py` using confirmed __NEXT_DATA__ path
- [ ] Backfill 2023-2025 moneyline odds for all Moneyline Core v1 pregame cards
- [ ] Compute: brain_calibrated_prob vs implied_prob from SBR consensus line
- [ ] Flag games where brain was right AND market disagreed (true edge signal)
- [ ] Do NOT use SBR odds to create trades or paper entries
- [ ] Do NOT modify Moneyline Core v1 rule until odds validation is complete