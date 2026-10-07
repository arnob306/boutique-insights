"""
Rolling-origin comparison of forecasting methods.

Pretend it is some past week (the origin), give each method only the weeks
before it, and ask for the next ``horizon`` weeks of demand. Then look at what
really sold. Repeat for origins stepping through history. A method can only
look good here by being right, because it never sees the answer.

Weeks that overlap a stock-out or lockdown are kept in the table but flagged
``scored=False``, since zero sales then say little about demand.
"""

from typing import Dict, List, Optional

import pandas as pd

from src.forecast.methods import METHODS, Method

DEFAULT_HORIZON = 4
DEFAULT_MIN_TRAIN = 104
DEFAULT_STEP = 4

RESULT_COLUMNS = ['target_start', 'product', 'method', 'forecast', 'actual', 'scored']
BOARD_COLUMNS = ['wape', 'bias', 'n']


def rolling_origins(n_weeks: int, horizon: int, min_train: int, step: int) -> List[int]:
    """Training lengths to test: each leaves ``horizon`` weeks to check against."""
    return list(range(min_train, n_weeks - horizon + 1, step))


def _require_at_least_one(**settings: int) -> None:
    for name, value in settings.items():
        if value < 1:
            raise ValueError(f'{name} must be at least 1, not {value}.')


def run_comparison(
    demand: pd.DataFrame,
    censored: pd.DataFrame,
    methods: Optional[Dict[str, Method]] = None,
    horizon: int = DEFAULT_HORIZON,
    min_train: int = DEFAULT_MIN_TRAIN,
    step: int = DEFAULT_STEP,
) -> pd.DataFrame:
    """One row per origin, product and method, with the forecast and the actual."""
    _require_at_least_one(horizon=horizon, min_train=min_train, step=step)
    if censored.shape != demand.shape:
        raise ValueError('The censored mask must have the same shape as the demand.')
    methods = METHODS if methods is None else methods
    units = demand.to_numpy()
    observed = ~censored.to_numpy()

    rows = []
    for origin in rolling_origins(len(demand), horizon, min_train, step):
        target = slice(origin, origin + horizon)
        for column, product in enumerate(demand.columns):
            history = units[:origin, column]
            seen = observed[:origin, column]
            actual = int(units[target, column].sum())
            scored = bool(observed[target, column].all())
            for name, method in methods.items():
                rows.append((demand.index[origin], product, name,
                             method(history, seen, horizon), actual, scored))
    return pd.DataFrame(rows, columns=RESULT_COLUMNS)


def _wape(abs_error: pd.Series, actual: pd.Series) -> pd.Series:
    """Total error over total units sold; undefined (NaN) if nothing sold."""
    return abs_error / actual.where(actual > 0)


def score(results: pd.DataFrame) -> pd.DataFrame:
    """
    WAPE, bias and count per method over the scored rows, best first.

    ``bias`` is the average forecast minus actual in units: positive means the
    method over-forecasts.
    """
    scored = results[results['scored'].astype(bool)]
    if scored.empty:
        return pd.DataFrame(columns=BOARD_COLUMNS)
    error = scored['forecast'] - scored['actual']
    grouped = pd.DataFrame({
        'abs_error': error.abs(), 'error': error, 'actual': scored['actual'],
    }).groupby(scored['method'])
    sums = grouped.sum()
    board = pd.DataFrame({
        'wape': _wape(sums['abs_error'], sums['actual']),
        'bias': grouped['error'].mean(),
        'n': grouped['error'].count(),
    })
    return board.sort_values('wape', na_position='last')


def score_by_year(results: pd.DataFrame) -> pd.DataFrame:
    """WAPE with one row per year of the forecast and one column per method."""
    scored = results[results['scored'].astype(bool)]
    if scored.empty:
        return pd.DataFrame()
    year = pd.to_datetime(scored['target_start']).dt.year.rename('year')
    error = (scored['forecast'] - scored['actual']).abs()
    sums = pd.DataFrame({'abs_error': error, 'actual': scored['actual']}).groupby(
        [year, scored['method']]).sum()
    by_year = _wape(sums['abs_error'], sums['actual']).unstack('method')
    return by_year[score(results).index]  # same best-first order as the scoreboard
