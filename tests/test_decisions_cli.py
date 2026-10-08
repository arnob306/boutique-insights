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


def _score_festival_forecasts(log_path, *rows):
    """rows: (festival, expected, low, high, actual)"""
    with open_log(log_path) as conn:
        for i, (festival, expected, low, high, actual) in enumerate(rows, start=1):
            conn.execute(
                'INSERT INTO festival_forecasts (festival, window_start, window_end, '
                'recorded_on, expected, low, high, lift_windows, backtest_windows) '
                "VALUES (?, '2026-10-10', '2026-10-21', '2026-09-01', ?, ?, ?, 4, 12)",
                (festival, expected, low, high))
            conn.execute(
                'INSERT INTO festival_outcomes (forecast_id, evaluated_on, actual, '
                'forecast_error, inside_range) VALUES (?, ?, ?, ?, ?)',
                (i, '2026-11-01', actual, expected - actual,
                 None if low is None else int(low <= actual <= high)))


def test_summary_shows_festival_forecast_accuracy_and_range_coverage(log_path, capsys):
    _score_festival_forecasts(
        log_path,
        ('Durga Puja', 1000.0, 600.0, 2000.0, 800.0),    # error +200, inside
        ('Diwali', 1000.0, 600.0, 2000.0, 2500.0))       # error -1500, above
    assert _run(log_path, 'summary') == 0
    out = capsys.readouterr().out
    assert 'no outcomes yet' not in out.lower()
    assert 'Festival sales forecasts' in out
    assert '2 scored' in out
    assert '52%' in out                 # (200 + 1500) / 3300
    assert 'range held in 1 of 2' in out


def test_summary_says_when_no_festival_forecast_had_a_range(log_path, capsys):
    _score_festival_forecasts(log_path, ('Durga Puja', 1000.0, None, None, 800.0))
    assert _run(log_path, 'summary') == 0
    assert 'no range to check' in capsys.readouterr().out.lower()


def _score_experiment(log_path, n_send, bought_send, n_hold, bought_hold):
    with open_log(log_path) as conn:
        conn.execute(
            'INSERT INTO experiments (festival, window_start, window_end, listed_on, '
            "last_start, last_end, holdout_share, n_send, n_hold) VALUES ('Diwali', "
            "'2026-10-10', '2026-10-21', '2026-09-20', '2025-10-11', '2025-10-22', "
            '0.2, ?, ?)', (n_send, n_hold))
        conn.execute('INSERT INTO experiment_outcomes VALUES (1, ?, ?, ?, ?, ?)',
                     ('2026-11-01', n_send, n_hold, bought_send, bought_hold))


def test_summary_reports_a_small_holdout_as_inconclusive(log_path, capsys):
    _score_experiment(log_path, 20, 9, 8, 3)
    assert _run(log_path, 'summary') == 0
    out = capsys.readouterr().out
    assert 'no outcomes yet' not in out.lower()
    assert 'Reminder holdout' in out
    assert '20 contacted' in out and '8 held back' in out
    assert '45%' in out and '38%' in out
    assert '95% interval' in out
    assert 'smallest difference' in out.lower()
    assert 'inconclusive' in out.lower()


def test_summary_reports_a_clear_holdout_result(log_path, capsys):
    _score_experiment(log_path, 400, 200, 100, 25)
    assert _run(log_path, 'summary') == 0
    assert 'positive' in capsys.readouterr().out.lower()


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
