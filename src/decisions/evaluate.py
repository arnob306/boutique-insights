"""
Fill in what actually happened, and score each model against it.

A recommendation's window is the ``horizon_weeks`` weeks after its date. Once
sales cover the whole window, units sold are counted (refunds are not demand)
and the forecast error is stored. Anything only she knows, like whether an item
sold out, is left empty rather than guessed.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from src.decisions.store import (
    Outcome,
    add_outcome,
    list_outcomes,
    list_pending,
    list_recommendations,
    open_log,
)

DAYS_PER_WEEK = 7


@dataclass(frozen=True)
class ModelSummary:
    """How one model's recommendations of one kind have turned out so far.

    ``wape`` is total absolute error over total units sold (None if nothing
    sold). ``bias`` is the mean signed error: positive means over-forecasting.
    """
    model: str
    kind: str
    n: int
    wape: Optional[float]
    bias: float


def evaluate_due(
    conn: sqlite3.Connection, sales: pd.DataFrame, evaluated_on: date
) -> int:
    """Write outcomes for every pending recommendation whose window is over."""
    if sales.empty:
        return 0
    data_through = sales['date'].max().normalize()
    demand = sales[sales['quantity'] > 0]
    written = 0
    for rec in list_pending(conn):
        start = pd.Timestamp(rec.as_of) + pd.Timedelta(days=1)
        end = pd.Timestamp(rec.as_of) + pd.Timedelta(
            days=rec.horizon_weeks * DAYS_PER_WEEK)
        if data_through < end:
            continue
        in_window = demand[
            (demand['product'] == rec.product)
            & (demand['date'] >= start) & (demand['date'] <= end)
        ]
        units = int(in_window['quantity'].sum())
        outcome = Outcome(
            recommendation_id=int(rec.id), evaluated_on=evaluated_on,
            units_sold=units, forecast_error=rec.expected_demand - units)
        written += add_outcome(conn, outcome)
    return written


def evaluate_log(sales: pd.DataFrame, log_path: Path, evaluated_on: date) -> int:
    """Open the log at ``log_path`` and score everything that is due."""
    with open_log(log_path) as conn:
        return evaluate_due(conn, sales, evaluated_on)


def summarise(conn: sqlite3.Connection) -> List[ModelSummary]:
    """One row per (model, kind) that has at least one outcome."""
    recommendations = {rec.id: rec for rec in list_recommendations(conn)}
    groups: Dict[Tuple[str, str], List[Outcome]] = {}
    for outcome in list_outcomes(conn):
        rec = recommendations[outcome.recommendation_id]
        groups.setdefault((rec.model, rec.kind), []).append(outcome)
    rows = []
    for (model, kind), outcomes in sorted(groups.items()):
        sold = sum(o.units_sold for o in outcomes)
        absolute_error = sum(abs(o.forecast_error) for o in outcomes)
        rows.append(ModelSummary(
            model=model, kind=kind, n=len(outcomes),
            wape=absolute_error / sold if sold > 0 else None,
            bias=sum(o.forecast_error for o in outcomes) / len(outcomes)))
    return rows
