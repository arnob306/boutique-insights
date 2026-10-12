"""
The model forecast for the weekly reorder advice.

Wraps the gradient-boosting model so the report can ask "how many units of each
product over the next N weeks" and never fail because of it. If scikit-learn is
missing or there is too little history, it returns nothing and the reorder
advice uses its older rule instead. The model sees only sales up to the as-of
date, and a fixed seed makes the same history give the same answer.
"""

from typing import Dict, Optional

import pandas as pd

from src.forecast.ml import trained_forecast
from src.forecast.series import censored_weeks, weekly_demand
from src.metrics.reorder import MODEL_BASIS, Forecaster

__all__ = ['MIN_HISTORY_WEEKS', 'MODEL_BASIS', 'model_forecaster']

MIN_HISTORY_WEEKS = 104


def model_forecaster(sales: pd.DataFrame, as_of) -> Optional[Forecaster]:
    """A function from horizon in weeks to expected units per product, or None.

    None means the model cannot be used at all (no scikit-learn, or under two
    years of history). The function itself returns None for a horizon it
    cannot train for.
    """
    try:
        import sklearn.ensemble  # noqa: F401
    except ImportError:
        return None
    as_of = pd.Timestamp(as_of).normalize()
    known = sales[sales['date'].dt.normalize() <= as_of]
    demand = weekly_demand(known, end=as_of)
    if len(demand) < MIN_HISTORY_WEEKS:
        return None
    starts = pd.DatetimeIndex(demand.index)
    units = demand.to_numpy()
    observed = ~censored_weeks(known, starts, demand.columns).to_numpy()

    def forecast(horizon_weeks: int) -> Optional[Dict[str, float]]:
        result = trained_forecast(units, observed, starts, horizon_weeks)
        if result is None:
            return None
        return {str(product): float(value)
                for product, value in zip(demand.columns, result, strict=True)}

    return forecast
