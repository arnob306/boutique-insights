"""
How much a festival is likely to sell, and so how much stock to commit.

The expected sales for a festival window are her recent ordinary daily sales
times that festival's usual lift (past window sales per day over the ordinary
level at the time) times the days in the window. On her real history this beat
last year's figure, last year scaled by growth, and the plain baseline.

The range comes from backtesting the same method on past festival windows,
each seeing only data from before an order would have been placed: the 10th
and 90th percentiles of actual over forecast. Festival sales are uneven, so the
range is wide and is shown as a range. This is a planning guide, not financial
advice.
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np
import pandas as pd

from src.metrics.patterns import festival_windows
from src.metrics.velocity import censored_windows

BASELINE_DAYS = 91
MIN_BASELINE_DAYS = 30  # ordinary days needed in the baseline period
MAX_STALE_DAYS = 14  # newest sale may be this far behind today
MIN_LIFT_WINDOWS = 2
MIN_BACKTEST_WINDOWS = 8
LOW_QUANTILE = 0.1
HIGH_QUANTILE = 0.9
DAYS_PER_WEEK = 7
DAY = pd.Timedelta(days=1)


@dataclass(frozen=True)
class CashPlan:
    festival: str
    expected: float  # expected sales in the festival window
    low: Optional[float]  # None when there is too little history for a range
    high: Optional[float]
    lift_windows: int  # past festivals the lift rests on
    backtest_windows: int  # past windows the range comes from
    budget_low: Optional[float]  # stock cost at the low and expected sales
    budget_expected: Optional[float]


class _History:
    """Daily sales and which days are ordinary, using only data before ``today``."""

    def __init__(self, sales: pd.DataFrame, today: pd.Timestamp):
        data = sales[sales['date'] < today]
        days = data['date'].dt.normalize()
        self.through = days.max()
        index = pd.date_range(days.min(), self.through)
        self.revenue = data.groupby(days)['line_total'].sum().reindex(
            index, fill_value=0.0)
        self.windows = festival_windows(data).sort_values('start')
        self.locked = pd.Series(False, index=index)
        for lock in censored_windows(data):
            if lock.product is None:
                self.locked[(index >= lock.start) & (index <= lock.end)] = True
        in_festival = pd.Series(False, index=index)
        for _, w in self.windows.iterrows():
            in_festival[(index >= w['start']) & (index <= w['end'])] = True
        self.ordinary = ~self.locked & ~in_festival

    def baseline(self, end: pd.Timestamp) -> Optional[float]:
        """Mean ordinary daily sales in the 91 days before ``end``."""
        period = slice(end - BASELINE_DAYS * DAY, end - DAY)
        ordinary = self.ordinary[period]
        if ordinary.sum() < MIN_BASELINE_DAYS:
            return None
        return float(self.revenue[period][ordinary].mean())

    def usable(self, start: pd.Timestamp, end: pd.Timestamp) -> bool:
        return end <= self.through and not self.locked[start:end].any()

    def lifts(self, festival: str, before: pd.Timestamp, lead: pd.Timedelta) -> List[float]:
        """Each past window's daily sales over the ordinary level at its order date."""
        past = self.windows[(self.windows['festival'] == festival)
                            & (self.windows['end'] < before)]
        ratios = []
        for _, w in past.iterrows():
            base = self.baseline(w['start'] - lead)
            if base and self.usable(w['start'], w['end']):
                ratios.append(float(self.revenue[w['start']:w['end']].mean() / base))
        return ratios

    def forecast(self, festival, start, end, origin, lead) -> Optional[float]:
        base = self.baseline(origin)
        ratios = self.lifts(festival, origin, lead)
        if not base or len(ratios) < MIN_LIFT_WINDOWS:
            return None
        return base * float(np.median(ratios)) * ((end - start).days + 1)


def _backtest(history: _History, lead: pd.Timedelta) -> List[float]:
    """Actual over forecast for each past window, forecast from its order date."""
    outcomes = []
    for _, w in history.windows.iterrows():
        if not history.usable(w['start'], w['end']):
            continue
        guess = history.forecast(w['festival'], w['start'], w['end'],
                                 w['start'] - lead, lead)
        if guess:
            outcomes.append(float(history.revenue[w['start']:w['end']].sum() / guess))
    return outcomes


def _times(value: Optional[float], factor: Optional[float]) -> Optional[float]:
    return None if value is None or factor is None else value * factor


def build_cash_plan(sales: pd.DataFrame, window, today, lead_weeks: int,
                    cost_share: Optional[float]) -> Optional[CashPlan]:
    """Expected sales, a likely range and a stock budget for ``window``.

    ``cost_share`` is her usual cost as a share of sales (1 minus margin).
    Returns None when there is too little history to say anything.
    """
    today = pd.Timestamp(today).normalize()
    if not (sales['date'] < today).any():
        return None
    history = _History(sales, today)
    if (today - history.through).days > MAX_STALE_DAYS:
        return None
    lead = pd.Timedelta(days=lead_weeks * DAYS_PER_WEEK)
    end = history.through + DAY
    ratios = history.lifts(window.name, end, lead)
    base = history.baseline(end)
    if not base or len(ratios) < MIN_LIFT_WINDOWS:
        return None
    days = (window.end - window.start).days + 1
    expected = base * float(np.median(ratios)) * days

    outcomes = _backtest(history, lead)
    ranged = len(outcomes) >= MIN_BACKTEST_WINDOWS
    low = expected * float(np.quantile(outcomes, LOW_QUANTILE)) if ranged else None
    high = expected * float(np.quantile(outcomes, HIGH_QUANTILE)) if ranged else None
    return CashPlan(
        festival=window.name, expected=expected, low=low, high=high,
        lift_windows=len(ratios), backtest_windows=len(outcomes),
        budget_low=_times(low, cost_share),
        budget_expected=_times(expected, cost_share))
