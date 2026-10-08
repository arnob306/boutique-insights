import sqlite3
from datetime import date

import pytest

from src.decisions.store import (
    SCHEMA_VERSION,
    DecisionLogError,
    Recommendation,
    add_recommendations,
    list_recommendations,
    open_log,
)

V1_SCHEMA = """
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

NEW_TABLES = {'festival_forecasts', 'festival_outcomes', 'experiments',
              'experiment_outcomes'}


@pytest.fixture
def v1_log(tmp_path):
    """A version 1 log that already holds a recommendation, an action and an outcome."""
    path = tmp_path / 'decisions.sqlite'
    raw = sqlite3.connect(path)
    raw.executescript(V1_SCHEMA)
    raw.execute(
        "INSERT INTO recommendations (as_of, product, kind, model, horizon_weeks, "
        "suggested_qty, expected_demand, confidence) "
        "VALUES ('2026-09-06', 'Silk Saree', 'forecast', 'velocity-v1', 4, 0, 6.5, 'good')")
    raw.execute(
        "INSERT INTO actions (recommendation_id, acted_on, product, action, qty) "
        "VALUES (1, '2026-09-07', 'Silk Saree', 'ordered', 8)")
    raw.execute(
        "INSERT INTO outcomes (recommendation_id, evaluated_on, units_sold, forecast_error) "
        "VALUES (1, '2026-10-05', 5, 1.5)")
    raw.execute('PRAGMA user_version = 1')
    raw.commit()
    raw.close()
    return path


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_schema_version_is_two():
    assert SCHEMA_VERSION == 2


def test_a_new_log_has_the_outcome_tracking_tables(tmp_path):
    with open_log(tmp_path / 'new.sqlite') as conn:
        assert NEW_TABLES <= _tables(conn)
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 2


def test_a_version_1_log_is_upgraded_to_version_2(v1_log):
    with open_log(v1_log) as conn:
        assert NEW_TABLES <= _tables(conn)
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 2


def test_the_upgrade_keeps_every_existing_row(v1_log):
    with open_log(v1_log) as conn:
        recs = list_recommendations(conn)
        counts = [conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
                  for t in ('recommendations', 'actions', 'outcomes')]
    assert [r.product for r in recs] == ['Silk Saree']
    assert counts == [1, 1, 1]


def test_an_upgraded_log_still_accepts_new_recommendations(v1_log):
    new = Recommendation(
        as_of=date(2026, 9, 13), product='Potli Bag', kind='forecast', model='velocity-v1',
        horizon_weeks=4, suggested_qty=0, expected_demand=3.0, confidence='low')
    with open_log(v1_log) as conn:
        assert add_recommendations(conn, [new]) == 1


def test_upgrading_twice_changes_nothing(v1_log):
    with open_log(v1_log):
        pass
    with open_log(v1_log) as conn:
        assert conn.execute('SELECT COUNT(*) FROM recommendations').fetchone()[0] == 1
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 2


def test_an_upgrade_that_fails_leaves_the_log_at_version_1(v1_log):
    raw = sqlite3.connect(v1_log)
    raw.execute('CREATE TABLE experiments (clash INTEGER)')  # blocks the upgrade
    raw.commit()
    raw.close()
    with pytest.raises(DecisionLogError):
        with open_log(v1_log):
            pass
    check = sqlite3.connect(v1_log)
    version = check.execute('PRAGMA user_version').fetchone()[0]
    tables = _tables(check)
    check.close()
    assert version == 1
    assert 'festival_forecasts' not in tables


def test_a_future_version_is_still_refused(tmp_path):
    path = tmp_path / 'future.sqlite'
    with open_log(path):
        pass
    raw = sqlite3.connect(path)
    raw.execute(f'PRAGMA user_version = {SCHEMA_VERSION + 1}')
    raw.commit()
    raw.close()
    with pytest.raises(DecisionLogError, match='version'):
        with open_log(path):
            pass
