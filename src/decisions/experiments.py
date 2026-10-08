"""
Log the reminder holdout and score it afterwards.

Only counts are stored: how many customers were on the list and how many were
held back. Scoring recomputes who was eligible from the hashed sales (the same
rule the list used), re-derives each customer's group from the keyed hash, and
counts who bought from the day after the list was made to the end of the
festival. No names or ids are written to the log.
"""

import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterable, Optional, Tuple

import pandas as pd

from src.decisions.store import open_log
from src.holdout import HOLD, SEND, Comparison, arm_for, compare, eligible_ids


def record_experiments(
    conn: sqlite3.Connection, lists: Iterable, share: float, listed_on: date
) -> int:
    """Log each festival list that held someone back; the first one is kept."""
    added = 0
    for item in lists:
        if item.held_back == 0:
            continue
        cursor = conn.execute(
            'INSERT OR IGNORE INTO experiments (festival, window_start, window_end, '
            'listed_on, last_start, last_end, holdout_share, n_send, n_hold) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (item.festival, item.start.date().isoformat(), item.end.date().isoformat(),
             listed_on.isoformat(), item.last_start.date().isoformat(),
             item.last_end.date().isoformat(), share, item.count, item.held_back))
        added += cursor.rowcount
    return added


def _buyers(sales: pd.DataFrame, ids: set, after: pd.Timestamp, through: pd.Timestamp) -> set:
    buys = sales[(sales['quantity'] > 0) & sales['customer_id'].isin(ids)]
    day = pd.to_datetime(buys['date']).dt.normalize()
    return set(buys[(day > after) & (day <= through)]['customer_id'])


def evaluate_experiments(
    conn: sqlite3.Connection, sales: pd.DataFrame, salt: str, evaluated_on: date
) -> int:
    """Score every experiment whose festival is over; returns how many."""
    if sales.empty:
        return 0
    data_through = pd.to_datetime(sales['date']).max().normalize()
    pending = conn.execute(
        'SELECT e.id, e.festival, e.window_start, e.window_end, e.listed_on, '
        'e.last_start, e.last_end, e.holdout_share FROM experiments e '
        'LEFT JOIN experiment_outcomes o ON o.experiment_id = e.id '
        'WHERE o.experiment_id IS NULL ORDER BY e.window_start, e.id').fetchall()
    written = 0
    for exp_id, festival, start, end, listed, last_start, last_end, share in pending:
        window_end = pd.Timestamp(end)
        if data_through < window_end:
            continue
        listed_on = pd.Timestamp(listed)
        eligible = eligible_ids(sales, pd.Timestamp(last_start), pd.Timestamp(last_end),
                                listed_on)
        year = pd.Timestamp(start).year
        arms = {i: arm_for(i, festival, year, salt, share) for i in eligible}
        bought = _buyers(sales, eligible, listed_on, window_end)
        sending = {i for i, a in arms.items() if a == SEND}
        holding = {i for i, a in arms.items() if a == HOLD}
        cursor = conn.execute(
            'INSERT OR IGNORE INTO experiment_outcomes (experiment_id, evaluated_on, '
            'n_send, n_hold, bought_send, bought_hold) VALUES (?, ?, ?, ?, ?, ?)',
            (exp_id, evaluated_on.isoformat(), len(sending), len(holding),
             len(sending & bought), len(holding & bought)))
        written += cursor.rowcount
    return written


def pooled_comparison(conn: sqlite3.Connection) -> Optional[Comparison]:
    """All scored festivals added together and compared, or None if none yet."""
    row = conn.execute(
        'SELECT COUNT(*), SUM(n_send), SUM(bought_send), SUM(n_hold), SUM(bought_hold) '
        'FROM experiment_outcomes').fetchone()
    if not row[0]:
        return None
    return compare(row[1], row[2], row[3], row[4])


def track_experiments(
    log_path: Path, lists: Iterable, share: Optional[float], sales: pd.DataFrame,
    salt: str, today: date
) -> Tuple[int, int]:
    """Score what is due, then log new lists; returns (recorded, scored)."""
    with open_log(log_path) as conn:
        scored = evaluate_experiments(conn, sales, salt, today)
        recorded = (record_experiments(conn, lists, share, today)
                    if share is not None else 0)
    return recorded, scored
