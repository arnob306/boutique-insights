"""
Expected units per product for a festival, and how far off that has been.

A product's expected units are its recent ordinary daily units, times the
usual lift of its category in that festival, times the days in the window. It
is the cash plan's method (``cashplan.py``) applied to units. The lift is
taken for the category, not the product, because one product's festival sales
are too few to trust.

The spread is each product's actual units over this forecast, on every past
festival window, forecast only from data before an order would have been
placed. Pooling the products gives enough cases to read a spread from.
"""

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.metrics.patterns import festival_windows
from src.metrics.velocity import censored_windows

BASELINE_DAYS = 91
MIN_BASELINE_DAYS = 30  # ordinary days needed in the baseline period
MAX_STALE_DAYS = 14  # newest sale may be this far behind today
MIN_LIFT_WINDOWS = 2
MIN_SPREAD_SAMPLES = 30  # product-windows needed before a spread is believed
DAYS_PER_WEEK = 7
DAY = pd.Timedelta(days=1)


class _UnitHistory:
    """Daily units per product, and which days are ordinary, before ``today``."""

    def __init__(self, sales: pd.DataFrame, today: pd.Timestamp):
        data = sales[sales['date'] < today]
        days = data['date'].dt.normalize()
        self.through = days.max()
        index = pd.date_range(days.min(), self.through)
        bought = data[data['quantity'] > 0]
        self.units = bought.groupby(
            [bought['date'].dt.normalize(), 'product'])['quantity'].sum().unstack(
            fill_value=0).reindex(index, fill_value=0)
        self.category = data.drop_duplicates('product').set_index(
            'product')['category'].to_dict()
        self.windows = festival_windows(data).sort_values('start')

        locks = censored_windows(data)
        self.locked = self._covered(index, [w for w in locks if w.product is None])
        self.stocked_out = {
            product: self._covered(index, [w for w in locks if w.product == product])
            for product in self.units.columns}
        in_festival = pd.Series(False, index=index)
        for _, w in self.windows.iterrows():
            in_festival[(index >= w['start']) & (index <= w['end'])] = True
        self.ordinary = ~self.locked & ~in_festival
        self._categories: Dict[str, pd.Series] = {}
        self._lifts: Dict[Tuple[str, str, pd.Timestamp], List[float]] = {}

    @staticmethod
    def _covered(index: pd.DatetimeIndex, windows) -> pd.Series:
        mask = pd.Series(False, index=index)
        for w in windows:
            mask[(index >= w.start) & (index <= w.end)] = True
        return mask

    def _category_units(self, category: str) -> pd.Series:
        if category not in self._categories:
            members = [p for p in self.units.columns if self.category.get(p) == category]
            self._categories[category] = self.units[members].sum(axis=1)
        return self._categories[category]

    @staticmethod
    def _baseline(units: pd.Series, ordinary: pd.Series, end: pd.Timestamp) -> Optional[float]:
        """Mean ordinary daily units in the 91 days before ``end``."""
        period = slice(end - BASELINE_DAYS * DAY, end - DAY)
        days = ordinary[period]
        if days.sum() < MIN_BASELINE_DAYS:
            return None
        return float(units[period][days].mean())

    def usable(self, start: pd.Timestamp, end: pd.Timestamp) -> bool:
        return end <= self.through and not self.locked[start:end].any()

    def lifts(self, festival: str, category: str, before: pd.Timestamp,
              lead: pd.Timedelta) -> List[float]:
        """Each past window's daily category units over the ordinary level then."""
        key = (festival, category, before)
        if key not in self._lifts:
            units = self._category_units(category)
            past = self.windows[(self.windows['festival'] == festival)
                                & (self.windows['end'] < before)]
            ratios = []
            for _, w in past.iterrows():
                base = self._baseline(units, self.ordinary, w['start'] - lead)
                if base and self.usable(w['start'], w['end']):
                    ratios.append(float(units[w['start']:w['end']].mean() / base))
            self._lifts[key] = ratios
        return self._lifts[key]

    def forecast(self, product: str, festival: str, start: pd.Timestamp,
                 end: pd.Timestamp, origin: pd.Timestamp,
                 lead: pd.Timedelta) -> Optional[float]:
        category = self.category.get(product)
        if category is None:
            return None
        ordinary = self.ordinary & ~self.stocked_out[product]
        base = self._baseline(self.units[product], ordinary, origin)
        ratios = self.lifts(festival, category, origin, lead)
        if not base or len(ratios) < MIN_LIFT_WINDOWS:
            return None
        return base * float(np.median(ratios)) * ((end - start).days + 1)


def unit_forecasts(sales: pd.DataFrame, window, today, lead_weeks: int) -> Dict[str, float]:
    """Expected units in ``window`` for each product that has a forecast."""
    today = pd.Timestamp(today).normalize()
    if not (sales['date'] < today).any():
        return {}
    history = _UnitHistory(sales, today)
    if (today - history.through).days > MAX_STALE_DAYS:
        return {}
    lead = pd.Timedelta(days=lead_weeks * DAYS_PER_WEEK)
    origin = history.through + DAY
    forecasts = {
        product: history.forecast(product, window.name, window.start, window.end,
                                  origin, lead)
        for product in history.units.columns}
    return {product: value for product, value in forecasts.items() if value}


def spread_cases(sales: pd.DataFrame, today, lead_weeks: int) -> Tuple[np.ndarray, np.ndarray]:
    """Each past case's forecast and its actual over forecast units.

    A case is one product on one past festival window. Both arrays are empty
    when there are fewer than ``MIN_SPREAD_SAMPLES`` cases.
    """
    none = (np.array([]), np.array([]))
    today = pd.Timestamp(today).normalize()
    if not (sales['date'] < today).any():
        return none
    history = _UnitHistory(sales, today)
    lead = pd.Timedelta(days=lead_weeks * DAYS_PER_WEEK)
    forecasts: List[float] = []
    ratios: List[float] = []
    for _, w in history.windows.iterrows():
        if not history.usable(w['start'], w['end']):
            continue
        origin = w['start'] - lead
        for product in history.units.columns:
            guess = history.forecast(product, w['festival'], w['start'], w['end'],
                                     origin, lead)
            if guess:
                actual = float(history.units[product][w['start']:w['end']].sum())
                forecasts.append(guess)
                ratios.append(actual / guess)
    if len(ratios) < MIN_SPREAD_SAMPLES:
        return none
    return np.array(forecasts), np.array(ratios)


def spread_ratios(sales: pd.DataFrame, today, lead_weeks: int) -> np.ndarray:
    """Actual over forecast units for every case, whatever its size."""
    return spread_cases(sales, today, lead_weeks)[1]
