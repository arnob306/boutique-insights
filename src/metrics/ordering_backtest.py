"""
Did ordering by the newsvendor rule beat simpler rules on past festivals?

For every past festival window the rules are run as they would have been on
the order date: forecast and spread from data before that date only. Each rule
is then scored on what actually sold. Three rules are compared:

- ``newsvendor``: the quantity ``ordering`` recommends, with the spread
  pooled over every product and size;
- ``newsvendor_by_size``: the same, with the spread read from the past cases
  whose forecast was closest in size;
- ``expected``: just the forecast, rounded up;
- ``expected_plus_buffer``: the forecast plus its square root, the same safety
  margin the weekly reorder advice uses.

Stock already on the shelf is ignored, so this compares the gross need.
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from src.metrics.calendar import FestivalWindow
from src.metrics.ordering import (
    critical_ratio,
    order_quantity,
    realised_profit,
    similar_cases,
)
from src.metrics.ordering_forecast import DAYS_PER_WEEK, spread_cases, unit_forecasts
from src.metrics.ordering_plan import unit_economics
from src.metrics.patterns import festival_windows
from src.metrics.velocity import censored_windows

RULES = ('newsvendor', 'newsvendor_by_size', 'expected', 'expected_plus_buffer')


@dataclass(frozen=True)
class RuleScore:
    name: str
    profit: float  # margin on what sold less the cost of what was left
    spend: float  # cost of everything ordered
    leftover_units: float
    missed_units: float
    fill_rate: float  # share of demand the order covered


@dataclass(frozen=True)
class BacktestResult:
    windows: int  # past festival windows with at least one case
    cases: int  # product-windows scored
    scores: Tuple[RuleScore, ...]


@dataclass
class _Tally:
    profit: float = 0.0
    spend: float = 0.0
    leftover: float = 0.0
    missed: float = 0.0
    met: float = 0.0
    demand: float = 0.0


def _quantities(units: float, forecasts: np.ndarray, spread: np.ndarray,
                ratio: float) -> Dict[str, int]:
    similar = similar_cases(units, forecasts, spread)
    return {
        'newsvendor': order_quantity(units, spread, ratio) or 0,
        'newsvendor_by_size': order_quantity(units, similar, ratio) or 0,
        'expected': math.ceil(units - 1e-9),
        'expected_plus_buffer': math.ceil(units + math.sqrt(units) - 1e-9),
    }


def backtest_ordering(sales: pd.DataFrame, lead_weeks: int,
                      resale_share: float) -> BacktestResult:
    """Score each rule on every past festival window it could have been run on."""
    tallies = {name: _Tally() for name in RULES}
    lead = pd.Timedelta(days=lead_weeks * DAYS_PER_WEEK)
    shut = [w for w in censored_windows(sales) if w.product is None]
    through = sales['date'].max()
    cases, used = 0, 0
    for _, w in festival_windows(sales).sort_values('start').iterrows():
        origin = w['start'] - lead
        if w['end'] > through or any(s.start <= w['end'] and s.end >= w['start'] for s in shut):
            continue
        if not (sales['date'] < origin).any():
            continue
        window = FestivalWindow(w['festival'], w['start'], w['end'])
        expected = unit_forecasts(sales, window, origin, lead_weeks)
        forecasts, spread = spread_cases(sales, origin, lead_weeks)
        if not expected or len(spread) == 0:
            continue
        economics = unit_economics(sales, origin)
        sold = sales[(sales['date'] >= w['start']) & (sales['date'] <= w['end'])
                     & (sales['quantity'] > 0)].groupby('product')['quantity'].sum()
        before = cases
        for product, units in expected.items():
            if product not in economics:
                continue
            price, cost = economics[product]
            demand = float(sold.get(product, 0))
            underage = price - cost
            overage = max(0.0, cost - resale_share * price)
            quantities = _quantities(units, forecasts, spread,
                                     critical_ratio(price, cost, resale_share))
            for name, quantity in quantities.items():
                tally = tallies[name]
                tally.profit += realised_profit(quantity, demand, underage, overage)
                tally.spend += quantity * cost
                tally.leftover += max(quantity - demand, 0.0)
                tally.missed += max(demand - quantity, 0.0)
                tally.met += min(quantity, demand)
                tally.demand += demand
            cases += 1
        used += cases > before
    scores: List[RuleScore] = [
        RuleScore(name, t.profit, t.spend, t.leftover, t.missed,
                  t.met / t.demand if t.demand else 0.0)
        for name, t in tallies.items()]
    return BacktestResult(windows=used, cases=cases, scores=tuple(scores))
