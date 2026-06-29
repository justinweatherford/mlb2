# Claude Downtime Handoff: MLB/Kalshi Baseball Brain Research

_Last updated: 2026-06-18_

This document summarizes what Justin and Atlas implemented, tested, and learned while Claude was unavailable. It is meant to let Claude re-enter the project without re-litigating the research path or accidentally contaminating the logic with lookahead.

## Current state

The project is in a strong research state. During Claude downtime, we did not intentionally change production live candidate generation, execution logic, or real trading behavior. The work focused on read-only, output-only research scripts that build and test baseball-context features across 2023, 2024, and 2025.

The main conclusion is that the pregame baseball brain is improving, but the most useful architecture is not “predict every game.” The better architecture is:

1. Identify side/winner lean spots.
2. Identify team scoring watch spots.
3. Identify team 5+ avoid/NO spots.
4. Identify games likely to become useful live-watch setups.
5. Identify noisy/avoid spots.
6. Only later compare these baseball signals against market price, bid/ask, depth, and execution.

## Absolute guardrail

No-lookahead integrity matters more than any result.

The user specifically emphasized that lookahead contamination will “contaminate our brain.” Every script below was designed around this rule:

- Features must be created using only information available before the game being graded.
- Rolling team features update only after all games on that date have had their feature rows created.
- Starter/pitcher features use prior appearances only.
- Current game events and final score are used only for outcome grading.
- No Kalshi/Vegas prices were used in these baseball-truth research scripts.
- These scripts are not EV models and not trade recommendation models.

## Major scripts created or used during downtime

### `pitcher_event_audit_preview.py`

Purpose: audit what pitcher/event data exists and whether it can support pitcher aggregations.

Findings:

- `mlb_play_events` has pitcher-linked rows through `pitcher_name`.
- The stored `outs` field is not reliable enough to use directly for pitcher outs.
- Estimated outs should be derived from `event_type` and description.
- Contact classification exists but is imperfect, so HR/FB and xFIP-style stats should be treated as research-only until validated further.

Key lesson: pitcher history can be built, but it should use cautious derived outs and pitcher-name fallback until stable pitcher IDs are available.

### `pitcher_aggregate_preview.py`

Purpose: build pitcher-season aggregate previews.

Findings:

- Historical pitcher aggregation is viable.
- 2025 produced 805 pitcher rows.
- 171 had high confidence, 267 medium, 367 low.
- Top innings totals looked sane enough for preview work.
- Homemade xFIP can be calculated, but HR/FB inputs are parser-sensitive.

Key lesson: use starter IP/start, RA9, K/BB, and confidence first. Treat homemade xFIP as a promising research feature, not a core trusted feature yet.

### `historical_team_context_preview_v2.py`

Purpose: build clean no-lookahead historical team context.

Important fixes:

- Normalized aliases: `ARI -> AZ`, `KCR -> KC`, `CHW -> CWS`, `OAK -> ATH`, `WSH -> WSN`.
- Filtered non-MLB teams that appeared in old data.
- Excluded games before regular season start.
- Built team context before each game without using the game itself.

2025 clean output:

- 2,475 final games included.
- 111 excluded.
- 4,950 context rows.
- 30 teams.
- High-confidence rows: 4,048.

Key lesson: this became the clean foundation for all historical team-context tests.

### `comeback_context_training_histctx.py`

Purpose: test comeback/rebound states using no-lookahead historical team context.

2025 results:

- 2,475 games.
- 44,905 comeback spots.
- 0 missing context.

Important 2025 rates:

- Down 1 early: eventually won 38.87%, tied or led later 64.8%, scored next 2 innings 55.94%.
- Down 2 early: eventually won 26.49%, tied or led later 46.29%, scored next 2 innings 55.3%.
- Down 3 early: eventually won 18.87%, tied or led later 32.53%, scored next 2 innings 55.59%.

Key lesson: final win probability decays sharply by deficit, but scoring next 2 innings remains surprisingly stable. This supports live rebound/scalp watchlists more than hold-to-settle comeback bets.

### `mlb_game_pattern_discovery_preview.py`

Purpose: wider historical pattern scan across comeback, lead protection, answer-back, F5/full-game profiles, and possible edge lanes.

Strong multi-season patterns:

- Down 1 early frequently ties/leads later.
- Down 2 early has meaningful rebound rate.
- Down 3 early is not a strong full-game win spot, but still has stable score-next-2 behavior.
- Up 1 early is fragile across all seasons.
- Up 1 late is much safer but still not lock-safe.
- Answer-back after allowing runs is stable early and much weaker late.
- F5 low games usually stay below 9.
- F5 high games usually reach 9+.

Promoted research lanes:

- `fragile_lead_fade_watch`
- `early_deficit_rebound_watch`
- `answer_back_rebound_watch`
- `f5_high_continuation_watch`
- `f5_low_stays_low_watch`

Watchlist only:

- Down 3 early tie/lead later.
- Big-deficit team-total micro rebound.
- F5 explosion stall fade.

Avoid promoting:

- Team identity hard rules.
- L10-only rules.
- Generic quiet-early buy-over logic.

### `historical_tier_pattern_audit.py`

Purpose: test whether team-context tiers add stable lift over same-season/same-state baselines.

Input:

- Seasons: 2023, 2024, 2025.
- Total state rows: 274,520.
- Response-after-allowed rows: 51,730.

Useful stable positives:

- Down 1 early + home: scored next 2 avg 60.3%, lift +4.6%.
- Down 1 early + L10 RPG 5.5+: eventually won 41.9%, lift +4.3%.
- Down 1 early + offense form 60+: eventually won 43.9%, tied/led later 69.0%.
- Down 1 early + opponent strength < 40: eventually won 49.3%, scored next 2 60.8%, tied/led later 73.4%.
- Down 1 F5 + home: scored next 2 60.0%.

Useful stable negatives:

- Down 1 early + away: eventually won 33.4%, scored next 2 49.7%.
- Down 1 early + team strength 40-45: eventually won 31.2%.
- Down 1 early + team strength < 40: eventually won 29.2%.
- Down 2 early + away: eventually won 21.5%, scored next 2 49.1%.

Key lesson: home/away and opponent weakness matter a lot. Weak opponents and weak leaders create the best rebound/fade maps. Team strength below 40 is a meaningful downgrade. These are baseball-truth lifts, not market EV.

### `pregame_matchup_profile_preview.py`

Purpose: build a pregame baseline model from no-lookahead team context.

Prediction types:

- Winner.
- Team runs 4+.
- Team runs 5+.
- Full total 9+.
- F5 total 4+.

Across 2023-2025:

- Total predictions: 38,619.
- Correct: 21,920.
- Overall success: 56.8%.

Model versus baseline:

- Winner: model 55.6%, home baseline 52.7%, lift +2.9%.
- Full total 9+: model 52.9%, baseline 50.7%, lift +2.2%.
- Team runs 4+: model 55.3%, baseline 56.2%, lift -0.9%.
- Team runs 5+: model 57.4%, baseline 57.8%, lift -0.4%.
- F5 total 4+: model 62.2%, baseline 62.8%, lift -0.6%.

Key lesson: winner logic had real but modest signal. Full total 9+ had weak positive signal. Team totals and F5 totals did not beat dumb base rates yet.

### `pregame_model_calibration_preview.py`

Purpose: compare model predictions against simple base-rate baselines.

Key lesson: existing pregame logic was not enough to support team-total confidence. It was useful mainly for winner/side direction and weak full-total context.

### `pregame_pitcher_context_preview_v2_fixed.py`

Purpose: add no-lookahead starter context using `pitcher_name` fallback and prior appearances only.

Input health:

- 2023: 2,471 games, 32,858 predictions.
- 2024: 2,560 games, 33,732 predictions.
- 2025: 2,475 games, 33,170 predictions.

Pitcher source health:

- Uses `pitcher_name` and `raw_json`.
- Starter lines: 2023 = 4,942, 2024 = 5,120, 2025 = 4,950.

Variant comparison:

- `team_context_only`: winner 55.6%, team runs 4+ 55.3%, team runs 5+ 57.4%, full total 9+ 52.9%.
- `starter_basic`: winner 56.1%, team runs 4+ 55.6%, team runs 5+ 57.8%, full total 9+ 53.9%.
- `starter_basic_plus_xfip`: winner 56.2%, team runs 4+ 56.0%, team runs 5+ 57.8%, full total 9+ 53.8%.

Key lesson: starter context helps modestly. Homemade xFIP adds a little on winner and team runs 4+. Starter context is a modifier, not a magic driver.

### `pregame_feature_family_lift_preview.py`

Purpose: test feature families and combinations to see what actually adds stable lift.

Feature families tested:

- Team quality.
- Offense consistency.
- Opponent vulnerability.
- Starter quality.
- Starter volatility.
- F5/post-F5 identity.
- Combo tags.
- Two-feature combinations.

Input:

- 2023: 4,942 team-game rows.
- 2024: 5,120 team-game rows.
- 2025: 4,950 team-game rows.

Feature family summary:

- Team quality: stable positive 29, stable negative 16, best positive lift +11.3%, best negative lift -11.5%.
- Offense consistency: stable positive 5, stable negative 8, best positive lift +5.9%.
- Opponent vulnerability: stable positive 12, stable negative 3, best positive lift +7.7%.
- Starter quality: stable positive 10, stable negative 18, best positive lift +4.8%.
- Starter volatility: stable positive 10, stable negative 7, best positive lift +5.5%.
- F5/post-F5 identity: stable positive 10, stable negative 0, best positive lift +8.6%.
- Combo tags: stable positive 12, stable negative 4, best positive lift +10.4%.

Top positive identifiers:

- Home team vs opponent strength < 40: team won 66.1%, lift +16.2%, count 599.
- Team strength 50-55 vs opponent strength < 40: team won 63.8%, lift +13.9%.
- L10 RPG 5.5+ vs opponent strength 40-45: team runs 5+ 56.2%, lift +13.1%; team won 62.9%, lift +12.9%; team F5 runs 2+ 69.9%, lift +11.6%.
- Weak leader fade watch: team won 60.4%, lift +10.4%; team runs 5+ 52.8%, lift +9.7%.

Top avoid/downweight identifiers:

- Team strength < 40 vs opponent strength 50-55: team won 35.9%, lift -14.1%; team runs 5+ 31.2%, lift -11.9%.
- Team strength < 40: team won 38.4%, lift -11.5%; team runs 5+ 34.4%, lift -8.7%; team runs 4+ 47.2%, lift -8.6%.

Key lesson: strongest identifiers are macro: opponent weakness, team strength gap, home team vs weak opponent, high recent scoring vs weak opponent, weak-leader fade/live-rebound tags, and F5/post-F5 identity. Pitcher stats help, but they are not dominant.

### `pregame_combined_identifier_score_preview.py`

Purpose: combine stable identifiers into composite scores and validate out-of-season.

Validation methods:

- Leave-one-season-out: train on two seasons, test on held-out season.
- Chronological: train 2023-2024, test 2025.

Input:

- 2023: 4,942 team-game rows.
- 2024: 5,120 team-game rows.
- 2025: 4,950 team-game rows.

Chronological 2025 results:

- Team won positive score at threshold 0.20: count 1,095, success 57.7%, baseline 50.0%, lift +7.7%.
- Game winner picks at threshold 0.20: picks 1,070, success 57.9%, home pick rate 58.2%.
- Team runs 4+ positive at threshold 0.15: count 1,376, success 59.4%, baseline 54.9%, lift +4.5%.
- Team runs 5+ negative/predict NO at threshold 0.20: count 703, success 65.1%, baseline 58.3%, lift +6.8%.
- Team F5 runs 2+ positive at threshold 0.20: count 1,086, success 61.3%, baseline 57.5%, lift +3.8%.

Leave-one-season-out winner picks:

- 2023: threshold 0.20, picks 1,367, success 56.8%.
- 2024: threshold 0.20, picks 1,010, success 59.3%.
- 2025: threshold 0.20, picks 1,070, success 57.9%.

Key lesson: this was the first truly meaningful “combined brain” improvement.

Current architecture after this:

- Green: winner/side lean, team runs 4+, team runs 5+ NO, team F5 runs 2+.
- Yellow: full total 9+ NO/avoid, F5 total 4+ positive research only, comeback avoid filters.
- Red: team runs 5+ YES, positive comeback prediction, F5 total NO, team F5 runs 2+ NO.

### `beans_offense_defense_lift_preview.py`

Purpose: create and test custom in-house offense/defense ratings.

Naming:

- `BO` = Beans Offense. In-house wRC+ style offensive creation index, not official FanGraphs wRC+.
- `BD` = Beans Defense. In-house run prevention and defensive chaos index, not official OAA or fielding metric.
- 100 = league-average-ish from prior rolling no-lookahead league environment.
- Above 100 is better for both BO and BD.

BO inputs:

- Runs per game.
- F5 runs.
- Post-5 runs.
- Scored 4+ rate.
- Scored 5+ rate.
- Scored 2 or fewer rate, inverted.
- Event-based BO variant: hits per game, walks per game, HR per game, scoring events per game.

BD inputs:

- Runs allowed per game, inverted.
- Allowed 4+ rate, inverted.
- Allowed 5+ rate, inverted.
- Allowed 2 or fewer rate.
- Post-5 runs allowed, inverted.
- BD chaos: error rate inverted, big inning allowed rate inverted.

Bullpen/rest features:

- Bullpen outs last 2 days.
- Reliever appearances last 2 days.
- Back-to-back reliever count.
- Starter short outing previous game.
- Bullpen-heavy previous game.
- Extra innings previous game.

Input:

- 2023: 4,942 team-game rows.
- 2024: 5,120 team-game rows.
- 2025: 4,950 team-game rows.

Stable outputs:

- BO plus weak BD tag: team runs 5+ 47.4%, lift +4.3%, count 2,907.
- Avoid low BO vs strong BD tag: team runs 5+ 38.7%, lift -4.5%, count 3,132.

Key lesson: BO/BD are useful but not magical. They mainly help team runs 5+ classification and avoid logic. BO/BD should be added to the combined brain as supporting features, not used as a standalone model. Raw error rate is noisy. Big inning allowed rate is a better defensive chaos proxy than errors. Bullpen fatigue alone was modest and should be used as a modifier only.

## Current best conclusions

### The combined pregame brain is meaningfully better than the start

Start:

- Winner around 55.6%.
- Team scoring mostly noisy.
- Totals mostly weak.

After combined identifiers:

- Winner can reach about 57-59% on selective spots.
- Team runs 4+ can reach around 59-65%, depending season/threshold.
- Team runs 5+ NO can reach around 63-65%.
- Team F5 runs 2+ can reach around 61-66%.
- Avoid logic is becoming genuinely useful.

### Best current edge shape

The edge is not:

- Predict every game.
- Find one magic stat.
- Bet every positive model output.

The edge is:

- Use stable baseball identifiers to classify which games are worth attention.
- Separate side, scoring, avoid, and live-watch lanes.
- Later compare those lanes to market pricing and execution.

### Most promising current lanes

1. Winner / side lean.
2. Team runs 4+ positive scoring watch.
3. Team runs 5+ NO / avoid.
4. Team F5 runs 2+ positive watch.
5. Weak-leader fade / live rebound watch.
6. Full total 9+ NO/avoid, research only.
7. F5 total 4+ positive, research only.

### Current avoid/downweight logic

Avoid or heavily downweight:

- Team strength < 40.
- Team strength gap -10 or worse.
- Weak team vs 50-55 opponent.
- Away team vs strong opponent.
- Mid/low scoring team vs excellent starter xFIP.
- Opponent workhorse starter for F5 scoring.
- Low BO vs strong opponent BD.
- Positive team runs 5+ YES from current combined score.
- Positive comeback prediction from current combined score.

## What data we are not yet using fully

Most promising missing data layers:

1. Weather and park environment:
   - Temperature, wind speed, wind direction, humidity, roof open/closed, park factors, day/night.
2. Confirmed/projected lineups:
   - Projected lineup wRC+, confirmed lineup wRC+, top-4 batter strength, bottom-3 weakness, missing star hitters, rest-day lineup downgrade.
3. Handedness:
   - Starter hand, team offense vs LHP/RHP, projected lineup vs starter hand, starter platoon split, bullpen platoon risk.
4. Bullpen availability:
   - We started rough usage tests, but need better reliever role and leverage data.
5. Defense:
   - We created BD and BD chaos, but still lack true fielding/OAA/catcher defense.
6. Market-side data:
   - Consensus sportsbook lines, line movement, Kalshi bid/ask/depth/last trade/volume/open interest.

## Recommended next implementation direction

Do not immediately change live candidate generation.

Recommended next safe step:

- Add BO/BD features into the combined identifier score preview.
- Re-run out-of-season validation.
- Check if the combined brain improves above:
  - Winner: 57.9%.
  - Team runs 4+: 59.4%.
  - Team runs 5+ NO: 65.1%.
  - Team F5 runs 2+: 61.3%.

After that, build a scored pregame slate card output:

- Side score.
- Side pick.
- Team runs 4+ score.
- Team runs 5+ NO score.
- Team F5 runs 2+ score.
- Full total avoid score.
- Live watch score.
- Avoid score.
- Top positive reasons.
- Top negative reasons.

Suggested script:

- `pregame_identifier_card_preview.py`

Suggested outputs:

- `pregame_side_leans.csv`
- `team_scoring_watchlist.csv`
- `team_5plus_avoid_list.csv`
- `team_f5_scoring_watchlist.csv`
- `live_watchlist.csv`
- `full_avoid_list.csv`
- `pregame_identifier_card_summary.md`

## Important wording for Claude

Do not frame this as a betting system yet.

Frame it as:

> A no-lookahead baseball-context classifier that identifies pregame side/scoring/avoid/live-watch setups. Market EV comes later after we compare these identifiers to Kalshi and sportsbook prices with realistic execution assumptions.

## Commands used most recently

Run combined identifier score preview:

```bat
cd /d "C:\Users\justi\OneDrive\Desktop\github\mlb2 - Copy"
python -m py_compile pregame_combined_identifier_score_preview.py
python pregame_combined_identifier_score_preview.py --seasons 2023 2024 2025
```

Run BO/BD preview:

```bat
cd /d "C:\Users\justi\OneDrive\Desktop\github\mlb2 - Copy"
python -m py_compile beans_offense_defense_lift_preview.py
python beans_offense_defense_lift_preview.py --seasons 2023 2024 2025
```

Optional BO/BD row-level debug:

```bat
python beans_offense_defense_lift_preview.py --seasons 2023 2024 2025 --write-team-game-rows
```

## Key scripts created during downtime

- `pitcher_event_audit_preview.py`
- `pitcher_aggregate_preview.py`
- `historical_team_context_preview_v2.py`
- `comeback_context_training_histctx.py`
- `mlb_game_pattern_discovery_preview.py`
- `historical_tier_pattern_audit.py`
- `pregame_matchup_profile_preview.py`
- `pregame_model_calibration_preview.py`
- `pregame_pitcher_context_preview_v2_fixed.py`
- `pregame_feature_family_lift_preview.py`
- `pregame_combined_identifier_score_preview.py`
- `beans_offense_defense_lift_preview.py`

## Most important output folders

- `outputs\pregame_feature_family_lift_preview\`
- `outputs\pregame_combined_identifier_score_preview\`
- `outputs\beans_offense_defense_lift_preview\`
- `outputs\historical_tier_pattern_audit\`
- `outputs\pregame_pitcher_context_preview_v2\`
- `outputs\pregame_model_calibration_preview\`
- `outputs\mlb_game_pattern_discovery\` or renamed season-specific equivalents.

## Final handoff summary

The system is in a good state.

The key insight from downtime is that the baseball brain is strongest when it is a classifier with multiple lanes, not a single winner/total predictor.

Most useful right now:

- Side/winner lean.
- Team runs 4+.
- Team runs 5+ NO/avoid.
- Team F5 runs 2+.
- Live-watch mapping.
- Avoid/downweight flags.

BO and BD are worth keeping as supporting features, especially for team 5+ and avoid logic, but they should be folded into the combined brain rather than used alone.

Do not let future implementation contaminate the data with lookahead. Validate every new feature with out-of-season or chronological testing before promoting it.
