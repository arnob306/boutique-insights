"""
A gradient-boosting challenger for the weekly demand forecast.

One model is trained across all products at each forecast origin, on rows
built only from weeks before that origin. Each row is a product at some past
week, described by how it had been selling, and the target is the units it
then sold over the next ``horizon`` weeks. Rows whose target weeks include a
stock-out or lockdown are left out, as in scoring. The loss is Poisson
because the target is a count with many small values.

This exists to test whether a heavier model beats the live velocity rule. It
is not used in the weekly report.
"""

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from src.forecast.methods import PanelMethod, velocity_rule

FEATURES = ('mean4', 'mean13', 'mean26', 'level52', 'last_year',
            'season_sin', 'season_cos')
FIRST_WEEK = 26  # a row needs this many weeks behind it
YEAR_WEEKS = 52
MIN_TRAIN_ROWS = 200  # fewer than this and the live rule is used instead
SEED = 0


def _recent_mean(units: np.ndarray, observed: np.ndarray, t: int, weeks: int) -> np.ndarray:
    """Mean units per week over the normal trading weeks just before ``t``."""
    span = slice(max(0, t - weeks), t)
    seen = observed[span]
    total = np.where(seen, units[span], 0.0).sum(axis=0)
    count = seen.sum(axis=0)
    return np.where(count > 0, total / np.maximum(count, 1), np.nan)


def _last_year(units: np.ndarray, observed: np.ndarray, t: int, horizon: int) -> np.ndarray:
    """Units in the same weeks a year earlier, if all were normal trading."""
    first = t - YEAR_WEEKS
    nothing = np.full(units.shape[1], np.nan)
    if first < 0:
        return nothing
    clean = observed[first:first + horizon].all(axis=0)
    return np.where(clean, units[first:first + horizon].sum(axis=0), np.nan)


def _features_at(units: np.ndarray, observed: np.ndarray, t: int, start,
                 horizon: int) -> pd.DataFrame:
    """One row of features per product, from the weeks before ``t`` only."""
    week = int(pd.Timestamp(start).isocalendar().week)
    angle = 2 * np.pi * week / YEAR_WEEKS
    count = units.shape[1]
    return pd.DataFrame({
        'mean4': _recent_mean(units, observed, t, 4),
        'mean13': _recent_mean(units, observed, t, 13),
        'mean26': _recent_mean(units, observed, t, 26),
        'level52': _recent_mean(units, observed, t, YEAR_WEEKS),
        'last_year': _last_year(units, observed, t, horizon),
        'season_sin': np.full(count, np.sin(angle)),
        'season_cos': np.full(count, np.cos(angle)),
    }, columns=list(FEATURES))


def training_rows(units: np.ndarray, observed: np.ndarray, starts: pd.DatetimeIndex,
                  horizon: int) -> Tuple[pd.DataFrame, np.ndarray]:
    """Features and targets for every product and week that has a full target.

    The index is (week position, product position).
    """
    frames: List[pd.DataFrame] = []
    targets: List[np.ndarray] = []
    for t in range(FIRST_WEEK, len(units) - horizon + 1):
        clean = observed[t:t + horizon].all(axis=0)
        if not clean.any():
            continue
        features = _features_at(units, observed, t, starts[t], horizon).iloc[
            np.flatnonzero(clean)]
        features.index = pd.MultiIndex.from_arrays(
            [np.full(int(clean.sum()), t), np.flatnonzero(clean)])
        frames.append(features)
        targets.append(units[t:t + horizon].sum(axis=0)[clean])
    if not frames:
        return pd.DataFrame(columns=list(FEATURES)), np.array([])
    return pd.concat(frames), np.concatenate(targets)


def gradient_boosting(units: np.ndarray, observed: np.ndarray, starts: pd.DatetimeIndex,
                      horizon: int) -> np.ndarray:
    """Expected units over the next ``horizon`` weeks, one value per product.

    Falls back to the live velocity rule when there is too little history to
    train on.
    """
    rows, targets = training_rows(units, observed, starts, horizon)
    if len(targets) < MIN_TRAIN_ROWS:
        return np.array([velocity_rule(units[:, i], observed[:, i], horizon)
                         for i in range(units.shape[1])])
    from sklearn.ensemble import HistGradientBoostingRegressor
    model = HistGradientBoostingRegressor(
        loss='poisson', max_iter=150, learning_rate=0.05, max_depth=3,
        min_samples_leaf=20, early_stopping=False, random_state=SEED)
    model.fit(rows.to_numpy(float), targets)
    now = _features_at(units, observed, len(units), starts[-1] + pd.Timedelta(days=7),
                       horizon)
    return np.clip(model.predict(now.to_numpy(float)), 0.0, None)


PANEL_METHODS: Dict[str, PanelMethod] = {'gradient boosting': gradient_boosting}
