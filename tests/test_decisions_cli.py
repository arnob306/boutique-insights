from datetime import date

import pytest

from src.decisions.__main__ import main
from src.decisions.store import (
    Outcome,
    Recommendation,
    add_outcome,
    add_recommendations,
    list_pending,
    open_log,
)

TODAY = date(2026, 10, 1)


def make_rec(**overrides):
    fields = dict(
        as_of=date(2026, 9, 6), product='Silk Saree', kind='reorder',
        model='reorder-v1', horizon_weeks=2, suggested_qty=8,
        expected_demand=6.0, confidence='good',
    )
    return Recommendation(**{**fields, **overrides})


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / 'decisions.sqlite'


def _run(log_path, *args):
    return main([*args, '--log', str(log_path)], today=lambda: TODAY)


def _actions(log_path):
    with open_log(log_path) as conn:
        return conn.execute(
            'SELECT recommendation_id, acted_on, product, action, qty FROM actions'
        ).fetchall()


def test_record_action_without_a_recommendation_is_standalone(log_path, capsys):
    code = _run(log_path, 'record-action', '--product', 'Potli Bag',
                '--action', 'ordered', '--qty', '20')
    assert code == 0
    assert _actions(log_path) == [(None, '2026-10-01', 'Potli Bag', 'ordered', 20)]
    assert 'not linked' in capsys.readouterr().out.lower()


def test_record_action_links_to_the_newest_earlier_reorder_recommendation(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [
            make_rec(as_of=date(2026, 8, 30)),                    # older
            make_rec(as_of=date(2026, 9, 6)),                     # the one to link
            make_rec(as_of=date(2026, 9, 13), kind='forecast',
                     model='velocity-v1', suggested_qty=0),       # wrong kind
            make_rec(as_of=date(2026, 9, 20), product='Potli Bag'),  # wrong product
            make_rec(as_of=date(2026, 10, 8)),                    # after the action
        ])
        wanted = next(r.id for r in list_pending(conn)
                      if r.as_of == date(2026, 9, 6))
    code = _run(log_path, 'record-action', '--product', 'Silk Saree',
                '--action', 'ordered', '--qty', '8')
    assert code == 0
    assert _actions(log_path)[0][0] == wanted


def test_record_action_uses_the_date_given(log_path):
    _run(log_path, 'record-action', '--product', 'Potli Bag', '--action', 'skipped',
         '--qty', '0', '--date', '2026-09-15')
    assert _actions(log_path)[0][1] == '2026-09-15'


def test_an_unknown_action_is_refused_by_the_parser(log_path):
    with pytest.raises(SystemExit) as exc:
        _run(log_path, 'record-action', '--product', 'Potli Bag',
             '--action', 'ignored', '--qty', '1')
    assert exc.value.code == 2


def test_a_bad_quantity_is_reported_not_a_traceback(log_path, capsys):
    code = _run(log_path, 'record-action', '--product', 'Potli Bag',
                '--action', 'ordered', '--qty', '-4')
    out = capsys.readouterr().out
    assert code == 2
    assert 'Cannot record' in out
    assert 'Traceback' not in out


def test_a_bad_date_is_reported_not_a_traceback(log_path, capsys):
    code = _run(log_path, 'record-action', '--product', 'Potli Bag',
                '--action', 'ordered', '--qty', '1', '--date', 'next week')
    assert code == 2
    assert 'date' in capsys.readouterr().out.lower()


def test_summary_without_a_log_says_so_and_creates_nothing(log_path, capsys):
    assert _run(log_path, 'summary') == 0
    assert 'no decision log' in capsys.readouterr().out.lower()
    assert not log_path.exists()


def test_summary_of_a_corrupt_log_is_reported_not_a_traceback(log_path, capsys):
    log_path.write_text('not a database', encoding='utf-8')
    code = _run(log_path, 'summary')
    out = capsys.readouterr().out
    assert code == 2
    assert 'Cannot read the decision log' in out
    assert 'Traceback' not in out


def test_summary_before_any_outcome_explains_why(log_path, capsys):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec()])
    assert _run(log_path, 'summary') == 0
    assert 'no outcomes yet' in capsys.readouterr().out.lower()


def test_summary_shows_error_and_bias_per_model(log_path, capsys):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec(expected_demand=6.0)])
        (rec,) = list_pending(conn)
        add_outcome(conn, Outcome(recommendation_id=rec.id, evaluated_on=TODAY,
                                  units_sold=5, forecast_error=1.0))
    assert _run(log_path, 'summary') == 0
    out = capsys.readouterr().out
    assert 'reorder-v1' in out
    assert '20%' in out    # WAPE: 1 unit wrong out of 5 sold
    assert '+1.0' in out   # over-forecast by one unit on average
