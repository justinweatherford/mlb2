# Market Feature Export — 2026-06-15

**Script version:** 1.0.0  
**Candidates:** 159  
**Snap source:** REST poll only (focused_watch data is post-midnight UTC)  
**Poll cadence:** ~4 minutes per market  

## Methodology

### Rows
One row per candidate event. market_feature_rows and candidate_feature_rows
are both candidate-centric; game_market_summary is one row per game.

### Price Context
- `prior_mid_300s`: latest snap strictly before candidate time (within 360s window)
- `next_mid_300s`: earliest snap strictly after candidate time (within 360s window)
- `delta_mid_next_300s`: next_mid_300s minus current_mid at candidate time
- `max/min_mid_next_300s`: price range in the 300s window after candidate time

### Process Grade
Derived from Tuning Pass 1 replay logic:
- `bad_process`: classification would change under Pass 1 rules
- `questionable_process`: mismatch score was inflated (first_discovery cap)
- `sound_process`: clean pass under both original and tuning rules
- `insufficient_context`: missing entry prices

### Team Strength
- `selected_team_strength_rating`: from mlb_team_context (composite seasonal rating)
- `selected_team_form_context`: L5 scoring form rating

### Weather
- `weather_run_label`: wre_label from mlb_weather_reference
  (neutral/volatile/favorable/not_applicable)

### Caveats
- All 159 candidates on 2026-06-15 have baseline_source=first_discovery
  (first live-capture day; no kalshi_open baselines established yet)
- mlb_play_events description/event_type may be NULL for this date
- focused_watch snapshots are from post-midnight UTC, not during game action