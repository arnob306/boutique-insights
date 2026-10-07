"""
Three ways to guess the next few weeks of demand for one product.

Each takes the weekly units sold so far, which of those weeks were normal
trading weeks (not a stock-out or lockdown), and how many weeks to look ahead.
It returns the expected total units over those weeks. They see only what is
passed in, so a backtest can hand them just the past.

- velocity rule: the live reorder rule, average of the last 13 weeks.
- recent mean: average of the last 8 weeks, quicker to notice a change.
- seasonal naive: the same weeks a year ago, which captures festivals.
"""

from typing import Callable, Dict

import numpy as np

VELOCITY_WEEKS = 13
RECENT_WEEKS = 8
SEASON_WEEKS = 52

Method = Callable[[np.ndarray, np.ndarray, int], float]


def _average_week(history: np.ndarray, observed: np.ndarray, weeks: int) -> float:
    """Mean units per week over the last ``weeks`` observed weeks (0 if none)."""
    kept = history[-weeks:][observed[-weeks:]]
    return float(kept.mean()) if kept.size else 0.0


def velocity_rule(history: np.ndarray, observed: np.ndarray, horizon: int) -> float:
    return _average_week(history, observed, VELOCITY_WEEKS) * horizon


def recent_mean(history: np.ndarray, observed: np.ndarray, horizon: int) -> float:
    return _average_week(history, observed, RECENT_WEEKS) * horizon


def seasonal_naive(history: np.ndarray, observed: np.ndarray, horizon: int) -> float:
    """
    Units sold in the same weeks a year ago.

    Falls back to the recent mean when there is not a full year of history,
    when the horizon is longer than a season, or when any of last year's weeks
    was not a normal trading week.
    """
    start = len(history) - SEASON_WEEKS
    if start < 0 or horizon > SEASON_WEEKS or not observed[start:start + horizon].all():
        return recent_mean(history, observed, horizon)
    return float(history[start:start + horizon].sum())


METHODS: Dict[str, Method] = {
    'velocity rule': velocity_rule,
    'recent mean': recent_mean,
    'seasonal naive': seasonal_naive,
}
