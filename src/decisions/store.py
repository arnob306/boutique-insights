"""
Decision log: what was recommended, what was done, and what happened next.

A small SQLite file under data/private/. It holds product names, dates and
quantities only: no customer data and no free-text columns, because free text
is where names leak in. Re-running a week never rewrites history: the first
recommendation recorded for a (week, product, kind, model) is kept.
"""

import math
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, cast

SCHEMA_VERSION = 1
DEFAULT_LOG_PATH = Path('data/private/decisions.sqlite')

KINDS = ('reorder', 'forecast')
ACTIONS = ('ordered', 'skipped', 'adjusted')
CONFIDENCES = ('low', 'medium', 'good')

_SCHEMA = """
CREATE TABLE recommendations (
    id INTEGER PRIMARY KEY,
    as_of TEXT NOT NULL,
    product TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('reorder', 'forecast')),
    model TEXT NOT NULL,
    horizon_weeks INTEGER NOT NULL CHECK (horizon_weeks >= 1),
    suggested_qty INTEGER NOT NULL CHECK (suggested_qty >= 0),
    expected_demand REAL NOT NULL CHECK (expected_demand >= 0),
    confidence TEXT NOT NULL CHECK (confidence IN ('low', 'medium', 'good')),
    UNIQUE (as_of, product, kind, model)
);
CREATE TABLE actions (
    id INTEGER PRIMARY KEY,
    recommendation_id INTEGER REFERENCES recommendations (id),
    acted_on TEXT NOT NULL,
    product TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('ordered', 'skipped', 'adjusted')),
    qty INTEGER NOT NULL CHECK (qty >= 0)
);
CREATE TABLE outcomes (
    recommendation_id INTEGER PRIMARY KEY REFERENCES recommendations (id),
    evaluated_on TEXT NOT NULL,
    units_sold INTEGER NOT NULL CHECK (units_sold >= 0),
    forecast_error REAL NOT NULL,
    leftover_units INTEGER CHECK (leftover_units >= 0),
    stocked_out INTEGER CHECK (stocked_out IN (0, 1))
);
"""


class DecisionLogError(RuntimeError):
    """A problem with the decision log, shown to the user as-is."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DecisionLogError(message)


@dataclass(frozen=True)
class Recommendation:
    as_of: date
    product: str
    kind: str
    model: str
    horizon_weeks: int
    suggested_qty: int
    expected_demand: float
    confidence: str
    id: Optional[int] = None

    def __post_init__(self) -> None:
        _require(self.kind in KINDS, f'Unknown recommendation kind: {self.kind!r}')
        _require(bool(self.product.strip()), 'A recommendation needs a product.')
        _require(bool(self.model.strip()), 'A recommendation needs a model name.')
        _require(self.horizon_weeks >= 1, 'The horizon must be at least one week.')
        _require(self.suggested_qty >= 0, 'The suggested quantity cannot be negative.')
        _require(math.isfinite(self.expected_demand) and self.expected_demand >= 0,
                 'Expected demand must be a number of zero or more.')
        _require(self.confidence in CONFIDENCES,
                 f'Unknown confidence: {self.confidence!r}')


@dataclass(frozen=True)
class Action:
    acted_on: date
    product: str
    action: str
    qty: int
    recommendation_id: Optional[int] = None

    def __post_init__(self) -> None:
        _require(self.action in ACTIONS, f'Unknown action: {self.action!r}')
        _require(bool(self.product.strip()), 'An action needs a product.')
        _require(self.qty >= 0, 'The quantity cannot be negative.')


@dataclass(frozen=True)
class Outcome:
    """What happened in a recommendation's window.

    ``forecast_error`` is expected minus actual units (positive means the
    forecast was too high). ``leftover_units`` and ``stocked_out`` are filled
    in by hand, because only she knows them.
    """
    recommendation_id: int
    evaluated_on: date
    units_sold: int
    forecast_error: float
    leftover_units: Optional[int] = None
    stocked_out: Optional[bool] = None

    def __post_init__(self) -> None:
        _require(self.units_sold >= 0, 'Units sold cannot be negative.')
        _require(math.isfinite(self.forecast_error), 'The forecast error must be a number.')
        _require(self.leftover_units is None or self.leftover_units >= 0,
                 'Leftover units cannot be negative.')


def _prepare(conn: sqlite3.Connection) -> None:
    """Create the schema on a new file, or refuse a file that is not ours."""
    conn.execute('PRAGMA foreign_keys = ON')
    version = conn.execute('PRAGMA user_version').fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    has_tables = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0]
    if version != 0 or has_tables:
        raise DecisionLogError(
            f'The decision log has schema version {version}, but this code '
            f'understands version {SCHEMA_VERSION}. Not touching it.')
    conn.executescript(_SCHEMA)
    conn.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')


@contextmanager
def open_log(path: Path = DEFAULT_LOG_PATH) -> Iterator[sqlite3.Connection]:
    """Open (creating if needed) the log; commit on success, roll back on error."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        try:
            _prepare(conn)
        except sqlite3.DatabaseError as exc:
            raise DecisionLogError(f'{path.name} is not a usable decision log.') from exc
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_recommendations(
    conn: sqlite3.Connection, recommendations: Sequence[Recommendation]
) -> int:
    """Record recommendations; returns how many were new (repeats are ignored)."""
    added = 0
    for rec in recommendations:
        cursor = conn.execute(
            'INSERT OR IGNORE INTO recommendations (as_of, product, kind, model, '
            'horizon_weeks, suggested_qty, expected_demand, confidence) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (rec.as_of.isoformat(), rec.product, rec.kind, rec.model,
             rec.horizon_weeks, rec.suggested_qty, rec.expected_demand,
             rec.confidence))
        added += cursor.rowcount
    return added


_RECOMMENDATION_COLUMNS = (
    'r.id, r.as_of, r.product, r.kind, r.model, r.horizon_weeks, '
    'r.suggested_qty, r.expected_demand, r.confidence')


def _recommendation_from_row(row: tuple) -> Recommendation:
    return Recommendation(
        id=row[0], as_of=date.fromisoformat(row[1]), product=row[2],
        kind=row[3], model=row[4], horizon_weeks=row[5],
        suggested_qty=row[6], expected_demand=row[7], confidence=row[8])


def list_recommendations(
    conn: sqlite3.Connection, kind: Optional[str] = None
) -> List[Recommendation]:
    """All recommendations, oldest first, optionally of one kind."""
    query = f'SELECT {_RECOMMENDATION_COLUMNS} FROM recommendations r'
    params: tuple = ()
    if kind is not None:
        query += ' WHERE r.kind = ?'
        params = (kind,)
    rows = conn.execute(query + ' ORDER BY r.as_of, r.id', params).fetchall()
    return [_recommendation_from_row(row) for row in rows]


def latest_recommendation_id(
    conn: sqlite3.Connection, product: str, kind: str, on_or_before: date
) -> Optional[int]:
    """The newest recommendation of this kind for a product, up to a date."""
    row = conn.execute(
        'SELECT id FROM recommendations WHERE product = ? AND kind = ? '
        'AND as_of <= ? ORDER BY as_of DESC, id DESC LIMIT 1',
        (product, kind, on_or_before.isoformat())).fetchone()
    return None if row is None else int(row[0])


def list_pending(conn: sqlite3.Connection) -> List[Recommendation]:
    """Recommendations that have no outcome yet, oldest first."""
    rows = conn.execute(
        f'SELECT {_RECOMMENDATION_COLUMNS} FROM recommendations r '
        'LEFT JOIN outcomes o ON o.recommendation_id = r.id '
        'WHERE o.recommendation_id IS NULL ORDER BY r.as_of, r.id').fetchall()
    return [_recommendation_from_row(row) for row in rows]


def add_action(conn: sqlite3.Connection, action: Action) -> int:
    """Record what was actually done; returns the new action's id."""
    try:
        cursor = conn.execute(
            'INSERT INTO actions (recommendation_id, acted_on, product, action, qty) '
            'VALUES (?, ?, ?, ?, ?)',
            (action.recommendation_id, action.acted_on.isoformat(),
             action.product, action.action, action.qty))
    except sqlite3.IntegrityError as exc:
        raise DecisionLogError(
            f'No recommendation with id {action.recommendation_id}.') from exc
    return cast(int, cursor.lastrowid)  # always set after a successful INSERT


def add_outcome(conn: sqlite3.Connection, outcome: Outcome) -> bool:
    """Record an outcome; False if that recommendation already has one."""
    stocked_out = None if outcome.stocked_out is None else int(outcome.stocked_out)
    try:
        cursor = conn.execute(
            'INSERT OR IGNORE INTO outcomes (recommendation_id, evaluated_on, '
            'units_sold, forecast_error, leftover_units, stocked_out) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (outcome.recommendation_id, outcome.evaluated_on.isoformat(),
             outcome.units_sold, outcome.forecast_error, outcome.leftover_units,
             stocked_out))
    except sqlite3.IntegrityError as exc:
        raise DecisionLogError(
            f'No recommendation with id {outcome.recommendation_id}.') from exc
    return cursor.rowcount == 1


def list_outcomes(conn: sqlite3.Connection) -> List[Outcome]:
    """All outcomes, in recommendation order."""
    rows = conn.execute(
        'SELECT recommendation_id, evaluated_on, units_sold, forecast_error, '
        'leftover_units, stocked_out FROM outcomes ORDER BY recommendation_id'
    ).fetchall()
    return [
        Outcome(
            recommendation_id=row[0], evaluated_on=date.fromisoformat(row[1]),
            units_sold=row[2], forecast_error=row[3], leftover_units=row[4],
            stocked_out=None if row[5] is None else bool(row[5]))
        for row in rows
    ]
