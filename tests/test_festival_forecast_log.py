from datetime import date

import pandas as pd
import pytest

from src.decisions.festival import (
    RECORD_STAGES,
    evaluate_festival_forecasts,
    festival_accuracy,
    record_festival_forecasts,
)
from src.decisions.store import open_log
from src.metrics.cashplan import CashPlan
from src.reports.playbook import PlaybookItem

START = pd.Timestamp('2026-10-10')
END = pd.Timestamp('2026-10-21')


def make_cash(**overrides):
    fields = dict(festival='Durga Puja', expected=1000.0, low=600.0, high=2000.0,
                  lift_windows=4, backtest_windows=12,
                  budget_low=300.0, budget_expected=500.0)
    return CashPlan(**{**fields, **overrides})


def make_item(stage='order_now', cash='default', festival='Durga Puja',
              start=START, end=END):
    plan = make_cash(festival=festival) if cash == 'default' else cash
    return PlaybookItem(
        festival=festival, start=start, end=end, stage=stage,
        order_by=start - pd.Timedelta(weeks=6), lead_weeks=6, lead_assumed=True,
        lifts=(), text='', cash=plan)


def sales_frame(*rows):
    frame = pd.DataFrame(rows, columns=['date', 'line_total'])
    return frame.assign(date=pd.to_datetime(frame['date']))


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / 'decisions.sqlite'


def record(log_path, items, recorded_on=date(2026, 9, 1)):
    with open_log(log_path) as conn:
        return record_festival_forecasts(conn, items, recorded_on)


# --- recording -------------------------------------------------------------

def test_a_forecast_is_recorded_when_it_is_time_to_order(log_path):
    assert record(log_path, [make_item('order_now')]) == 1
    assert record(log_path, [make_item('order_soon', festival='Diwali')]) == 1


@pytest.mark.parametrize('stage', ['later', 'preview', 'on_now', 'clearance'])
def test_other_stages_are_not_recorded(log_path, stage):
    assert stage not in RECORD_STAGES
    assert record(log_path, [make_item(stage)]) == 0


def test_an_item_with_no_cash_plan_is_not_recorded(log_path):
    assert record(log_path, [make_item('order_now', cash=None)]) == 0


def test_the_first_forecast_for_a_window_is_kept(log_path):
    record(log_path, [make_item('order_soon')], date(2026, 8, 20))
    later = make_item('order_now', cash=make_cash(expected=5000.0))
    assert record(log_path, [later], date(2026, 9, 3)) == 0
    with open_log(log_path) as conn:
        rows = conn.execute(
            'SELECT expected, recorded_on FROM festival_forecasts').fetchall()
    assert rows == [(1000.0, '2026-08-20')]


def test_the_same_festival_next_year_is_a_new_forecast(log_path):
    record(log_path, [make_item()])
    next_year = make_item(start=START + pd.Timedelta(days=364),
                          end=END + pd.Timedelta(days=364))
    assert record(log_path, [next_year]) == 1


def test_the_range_is_stored_even_when_missing(log_path):
    record(log_path, [make_item(cash=make_cash(low=None, high=None))])
    with open_log(log_path) as conn:
        row = conn.execute('SELECT low, high FROM festival_forecasts').fetchone()
    assert row == (None, None)


# --- scoring ---------------------------------------------------------------

def evaluate(log_path, sales, on=date(2026, 11, 1)):
    with open_log(log_path) as conn:
        return evaluate_festival_forecasts(conn, sales, on)


def outcome_rows(log_path):
    with open_log(log_path) as conn:
        return conn.execute(
            'SELECT actual, forecast_error, inside_range FROM festival_outcomes'
        ).fetchall()


def test_a_forecast_is_not_scored_before_its_window_has_passed(log_path):
    record(log_path, [make_item()])
    sales = sales_frame(('2026-10-10', 400.0), ('2026-10-20', 300.0))  # data stops 20 Oct
    assert evaluate(log_path, sales) == 0


def test_actual_is_the_net_sales_inside_the_window(log_path):
    record(log_path, [make_item()])
    sales = sales_frame(
        ('2026-10-09', 999.0),   # the day before: not in the window
        ('2026-10-10', 400.0),   # first day counts
        ('2026-10-15', -50.0),   # a refund reduces net sales
        ('2026-10-21', 450.0),   # last day counts
        ('2026-10-22', 999.0))   # after the window
    assert evaluate(log_path, sales) == 1
    actual, error, inside = outcome_rows(log_path)[0]
    assert actual == pytest.approx(800.0)
    assert error == pytest.approx(200.0)  # expected 1000 minus actual 800
    assert inside == 1


def test_a_result_outside_the_range_is_flagged(log_path):
    record(log_path, [make_item()])
    sales = sales_frame(('2026-10-12', 100.0), ('2026-10-22', 1.0))  # 100 < low of 600
    evaluate(log_path, sales)
    assert outcome_rows(log_path)[0][2] == 0


def test_the_range_edges_count_as_inside(log_path):
    record(log_path, [make_item()])
    sales = sales_frame(('2026-10-12', 600.0), ('2026-10-22', 1.0))  # exactly the low
    evaluate(log_path, sales)
    assert outcome_rows(log_path)[0][2] == 1


def test_no_range_means_no_verdict_on_the_range(log_path):
    record(log_path, [make_item(cash=make_cash(low=None, high=None))])
    evaluate(log_path, sales_frame(('2026-10-12', 700.0), ('2026-10-22', 1.0)))
    assert outcome_rows(log_path)[0][2] is None


def test_scoring_twice_writes_once(log_path):
    record(log_path, [make_item()])
    sales = sales_frame(('2026-10-12', 700.0), ('2026-10-22', 1.0))
    assert evaluate(log_path, sales) == 1
    assert evaluate(log_path, sales) == 0


def test_empty_sales_scores_nothing(log_path):
    record(log_path, [make_item()])
    assert evaluate(log_path, sales_frame()) == 0


# --- accuracy --------------------------------------------------------------

def accuracy(log_path):
    with open_log(log_path) as conn:
        return festival_accuracy(conn)


def test_accuracy_is_none_until_something_is_scored(log_path):
    record(log_path, [make_item()])
    assert accuracy(log_path) is None


def test_accuracy_summarises_error_bias_and_range_coverage(log_path):
    a = make_item(festival='Durga Puja')
    b = make_item(festival='Diwali', cash=make_cash(festival='Diwali'),
                  start=pd.Timestamp('2026-10-22'), end=pd.Timestamp('2026-10-31'))
    record(log_path, [a, b])
    sales = sales_frame(
        ('2026-10-12', 800.0),   # Durga Puja actual 800: error +200, inside range
        ('2026-10-25', 2500.0),  # Diwali actual 2500: error -1500, above range
        ('2026-11-01', 1.0))
    evaluate(log_path, sales)
    result = accuracy(log_path)
    assert result.n == 2
    assert result.wape == pytest.approx((200 + 1500) / (800 + 2500))
    assert result.bias == pytest.approx((200 - 1500) / (800 + 2500))
    assert (result.ranged, result.inside) == (2, 1)


def test_range_coverage_ignores_forecasts_without_a_range(log_path):
    record(log_path, [make_item(cash=make_cash(low=None, high=None))])
    evaluate(log_path, sales_frame(('2026-10-12', 700.0), ('2026-10-22', 1.0)))
    result = accuracy(log_path)
    assert (result.n, result.ranged, result.inside) == (1, 0, 0)
