import sqlite3
from datetime import date

import pytest

from src.decisions.store import (
    SCHEMA_VERSION,
    Action,
    DecisionLogError,
    Recommendation,
    add_action,
    add_recommendations,
    list_recommendations,
    open_log,
)

TODAY = date(2026, 9, 30)


def make_rec(**overrides):
    fields = dict(
        as_of=TODAY, product='Silk Saree', kind='forecast', model='velocity-v1',
        horizon_weeks=4, suggested_qty=0, expected_demand=6.5, confidence='good',
    )
    return Recommendation(**{**fields, **overrides})


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / 'private' / 'decisions.sqlite'


def test_open_log_creates_file_folders_and_tables(log_path):
    with open_log(log_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert log_path.exists()
    assert {'recommendations', 'actions', 'outcomes'} <= tables


def test_open_log_stamps_the_schema_version(log_path):
    with open_log(log_path) as conn:
        version = conn.execute('PRAGMA user_version').fetchone()[0]
    assert version == SCHEMA_VERSION


def test_reopening_keeps_existing_rows(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec()])
    with open_log(log_path) as conn:
        assert len(list_recommendations(conn)) == 1


def test_newer_schema_version_fails_closed(log_path):
    with open_log(log_path):
        pass
    raw = sqlite3.connect(log_path)
    raw.execute(f'PRAGMA user_version = {SCHEMA_VERSION + 1}')
    raw.commit()
    raw.close()
    with pytest.raises(DecisionLogError, match='version'):
        with open_log(log_path):
            pass


def test_a_file_that_is_not_a_log_is_refused(log_path):
    log_path.parent.mkdir(parents=True)
    log_path.write_text('this is not a database', encoding='utf-8')
    with pytest.raises(DecisionLogError):
        with open_log(log_path):
            pass


def test_add_recommendations_returns_how_many_were_new(log_path):
    with open_log(log_path) as conn:
        added = add_recommendations(conn, [make_rec(), make_rec(product='Potli Bag')])
    assert added == 2


def test_rerunning_the_same_week_adds_nothing(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec()])
        again = add_recommendations(conn, [make_rec(expected_demand=99.0)])
        rows = list_recommendations(conn)
    assert again == 0
    assert len(rows) == 1
    assert rows[0].expected_demand == 6.5  # first record wins, history is not rewritten


def test_same_product_different_kind_is_a_separate_row(log_path):
    with open_log(log_path) as conn:
        added = add_recommendations(
            conn, [make_rec(kind='forecast'), make_rec(kind='reorder', suggested_qty=5)])
    assert added == 2


def test_list_recommendations_round_trips_every_field(log_path):
    rec = make_rec(kind='reorder', suggested_qty=12, expected_demand=3.25,
                   confidence='low')
    with open_log(log_path) as conn:
        add_recommendations(conn, [rec])
        (stored,) = list_recommendations(conn)
    assert stored.as_of == rec.as_of
    assert (stored.product, stored.kind, stored.model) == (
        rec.product, rec.kind, rec.model)
    assert (stored.horizon_weeks, stored.suggested_qty) == (4, 12)
    assert stored.expected_demand == 3.25
    assert stored.confidence == 'low'
    assert isinstance(stored.id, int)


def test_list_recommendations_can_filter_by_kind(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec(), make_rec(kind='reorder', product='A')])
        rows = list_recommendations(conn, kind='reorder')
    assert [r.product for r in rows] == ['A']


@pytest.mark.parametrize('overrides', [
    {'kind': 'guess'},
    {'product': '  '},
    {'horizon_weeks': 0},
    {'suggested_qty': -1},
    {'expected_demand': -0.5},
    {'confidence': 'certain'},
])
def test_invalid_recommendations_are_rejected(overrides):
    with pytest.raises(DecisionLogError):
        make_rec(**overrides)


def test_action_can_point_at_a_recommendation(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec(kind='reorder', suggested_qty=8)])
        (rec,) = list_recommendations(conn)
        action_id = add_action(conn, Action(
            acted_on=TODAY, product='Silk Saree', action='ordered', qty=8,
            recommendation_id=rec.id))
        row = conn.execute(
            'SELECT recommendation_id, qty FROM actions WHERE id = ?',
            (action_id,)).fetchone()
    assert row == (rec.id, 8)


def test_action_without_a_recommendation_is_allowed(log_path):
    with open_log(log_path) as conn:
        action_id = add_action(conn, Action(
            acted_on=TODAY, product='Potli Bag', action='ordered', qty=20))
        row = conn.execute(
            'SELECT recommendation_id FROM actions WHERE id = ?',
            (action_id,)).fetchone()
    assert row == (None,)


def test_action_for_unknown_recommendation_is_refused(log_path):
    with open_log(log_path) as conn:
        with pytest.raises(DecisionLogError, match='recommendation'):
            add_action(conn, Action(
                acted_on=TODAY, product='Potli Bag', action='ordered', qty=1,
                recommendation_id=999))


@pytest.mark.parametrize('overrides', [
    {'action': 'ignored'},
    {'qty': -3},
    {'product': ''},
])
def test_invalid_actions_are_rejected(overrides):
    fields = dict(acted_on=TODAY, product='Potli Bag', action='ordered', qty=1)
    with pytest.raises(DecisionLogError):
        Action(**{**fields, **overrides})


def test_skipped_action_may_have_zero_quantity(log_path):
    with open_log(log_path) as conn:
        add_action(conn, Action(
            acted_on=TODAY, product='Potli Bag', action='skipped', qty=0))


def test_log_holds_no_customer_or_free_text_columns(log_path):
    with open_log(log_path) as conn:
        columns = {
            row[1]
            for table in ('recommendations', 'actions', 'outcomes')
            for row in conn.execute(f'PRAGMA table_info({table})')
        }
    for banned in ('customer', 'suburb', 'name', 'note', 'comment'):
        assert not any(banned in column for column in columns), banned
