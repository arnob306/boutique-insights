"""
Did the festival sales forecast hold?

Each festival's expected sales and range are recorded once, in the weeks when
there is still time to order (the first one is kept, so a later run never
rewrites history). When the festival window has passed, the actual net sales
are written next to it, together with whether they fell inside the range.

Planning support only: the numbers say how well the forecast did, not whether
following it made money.
"""

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Optional, Tuple

import pandas as pd

from src.decisions.store import open_log

RECORD_STAGES = ('order_soon', 'order_now')


@dataclass(frozen=True)
class FestivalAccuracy:
    """How the scored festival forecasts have turned out.

    ``wape`` is total absolute error over total actual sales, ``bias`` is total
    signed error over total actual sales (positive means the forecast was too
    high). ``ranged`` forecasts had a range, and ``inside`` of them held.
    """
    n: int
    wape: Optional[float]
    bias: Optional[float]
    ranged: int
    inside: int


def record_festival_forecasts(
    conn: sqlite3.Connection, playbook: Iterable, recorded_on: date
) -> int:
    """Log the forecast of every playbook item that is at an ordering stage.

    Returns how many were new; a window that is already logged is left alone.
    """
    added = 0
    for item in playbook:
        cash = item.cash
        if item.stage not in RECORD_STAGES or cash is None:
            continue
        cursor = conn.execute(
            'INSERT OR IGNORE INTO festival_forecasts (festival, window_start, '
            'window_end, recorded_on, expected, low, high, lift_windows, '
            'backtest_windows) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (item.festival, item.start.date().isoformat(), item.end.date().isoformat(),
             recorded_on.isoformat(), cash.expected, cash.low, cash.high,
             cash.lift_windows, cash.backtest_windows))
        added += cursor.rowcount
    return added


def _inside(actual: float, low: Optional[float], high: Optional[float]) -> Optional[int]:
    if low is None or high is None:
        return None
    return int(low <= actual <= high)


def evaluate_festival_forecasts(
    conn: sqlite3.Connection, sales: pd.DataFrame, evaluated_on: date
) -> int:
    """Score every logged forecast whose window is over; returns how many."""
    if sales.empty:
        return 0
    data_through = sales['date'].max().normalize()
    pending = conn.execute(
        'SELECT f.id, f.window_start, f.window_end, f.expected, f.low, f.high '
        'FROM festival_forecasts f LEFT JOIN festival_outcomes o ON o.forecast_id = f.id '
        'WHERE o.forecast_id IS NULL ORDER BY f.window_start, f.id').fetchall()
    written = 0
    for forecast_id, start, end, expected, low, high in pending:
        window_end = pd.Timestamp(end)
        if data_through < window_end:
            continue
        in_window = sales[(sales['date'] >= pd.Timestamp(start))
                          & (sales['date'] < window_end + pd.Timedelta(days=1))]
        actual = float(in_window['line_total'].sum())
        cursor = conn.execute(
            'INSERT OR IGNORE INTO festival_outcomes (forecast_id, evaluated_on, '
            'actual, forecast_error, inside_range) VALUES (?, ?, ?, ?, ?)',
            (forecast_id, evaluated_on.isoformat(), actual, expected - actual,
             _inside(actual, low, high)))
        written += cursor.rowcount
    return written


def track_festival_forecasts(
    log_path: Path, playbook: Iterable, sales: pd.DataFrame, today: date
) -> Tuple[int, int]:
    """Score what is due, then log what is new; returns (recorded, scored)."""
    with open_log(log_path) as conn:
        scored = evaluate_festival_forecasts(conn, sales, today)
        recorded = record_festival_forecasts(conn, playbook, today)
    return recorded, scored


def festival_accuracy(conn: sqlite3.Connection) -> Optional[FestivalAccuracy]:
    """Error, bias and range coverage over all scored forecasts, or None."""
    rows = conn.execute(
        'SELECT actual, forecast_error, inside_range FROM festival_outcomes').fetchall()
    if not rows:
        return None
    actual = sum(r[0] for r in rows)
    verdicts = [r[2] for r in rows if r[2] is not None]
    return FestivalAccuracy(
        n=len(rows),
        wape=sum(abs(r[1]) for r in rows) / actual if actual > 0 else None,
        bias=sum(r[1] for r in rows) / actual if actual > 0 else None,
        ranged=len(verdicts), inside=sum(verdicts))
