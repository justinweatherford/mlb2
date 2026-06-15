"""
mlb/sample_weight.py — Sample-size damping for baseball intelligence ratings.

Thin samples should not drive high-confidence ratings. These utilities apply
a linear ramp: full weight at `full_n` samples, zero weight at 0 samples.

Formula:
    sample_weight  = min(sample_n / full_n, 1.0)
    adjusted_rating = 50 + sample_weight * (raw_rating - 50)

Interpretation:
  - 0 samples → always returns 50 (neutral / league average)
  - full_n or more samples → returns raw_rating unchanged
  - Intermediate samples → linear blend toward 50

This is intentionally conservative.  Outlier events are retained in the raw data
(they did happen) but damped in the rating until repeated consistently.

Spread / F5 spread notes:
  The system currently requires is_semantics_clear=True before any candidate can
  be generated for spread markets (see kalshi/semantics.py).  Sample-size rules
  are a secondary layer; they apply to any baseball reads built on historical data.
"""

# Minimum sample count for a raw rating to reach full weight.
# Below this, ratings are blended toward 50 (neutral).
SAMPLE_FULL_N = 10

# Minimum sample count before a spread-related historical metric is
# used in any automated inference.  Below this, treat as "no data".
SPREAD_MIN_SAMPLE_N = 5


def compute_sample_weight(n: int, full_n: int = SAMPLE_FULL_N) -> float:
    """
    Return a weight in [0.0, 1.0] based on sample count n.

    At n=0  → 0.0  (no weight; use neutral baseline)
    At n>=full_n → 1.0  (full confidence in the raw rating)
    Between 0 and full_n → linear ramp.
    """
    if full_n <= 0:
        return 1.0
    return min(float(n) / float(full_n), 1.0)


def apply_sample_weight(
    raw_rating: float,
    n: int,
    full_n: int = SAMPLE_FULL_N,
    neutral: float = 50.0,
) -> float:
    """
    Damp raw_rating toward neutral (default 50) by sample count.

    Returns a value between neutral and raw_rating, inclusive.
    """
    w = compute_sample_weight(n, full_n)
    return neutral + w * (raw_rating - neutral)


def is_sufficient_for_spread(n: int) -> bool:
    """
    Return True only if the sample count meets the minimum threshold for
    spread-related historical inference.

    NOTE: this does NOT unlock spread Watch candidates — that requires
    is_semantics_clear=True from Kalshi metadata parsing.  This is for
    historical metric quality only.
    """
    return n >= SPREAD_MIN_SAMPLE_N
