import pandas as pd
import pytest
from test_metrics import make_sales

from src.metrics.calendar import FestivalWindow
from src.metrics.cashplan import (
    MIN_BACKTEST_WINDOWS,
    CashPlan,
    build_cash_plan,
)

DAY = pd.Timedelta(days=1)
TODAY = pd.Timestamp('2025-08-20')
LEAD_WEEKS = 6
DAILY = 100.0  # an ordinary day's sales

# (name, month, first day, last day)
FESTIVALS = (('Saraswati Puja', 2, 1, 11), ('Eid', 3, 3, 11),
             ('Durga Puja', 10, 10, 20), ('Christmas', 12, 12, 25))


def _history(festivals=FESTIVALS, first_year=2019, last_day=TODAY - DAY,
             boost=lambda year: 3, lockdown=None):
    """One $100 sale a day, and ``boost(year)`` sales a day inside each festival."""
    windows = [(name, pd.Timestamp(year=year, month=month, day=first),
                pd.Timestamp(year=year, month=month, day=last))
               for name, month, first, last in festivals
               for year in range(first_year, last_day.year + 1)]
    rows = []
    for date in pd.date_range(f'{first_year}-01-01', last_day):
        label = next((name for name, a, b in windows if a <= date <= b), None)
        event = 'Lockdown' if lockdown and lockdown[0] <= date <= lockdown[1] else None
        count = boost(date.year) if label else 1
        rows += [{'date': date, 'price': DAILY, 'festival': label,
                  'event': event}] * count
    return make_sales(rows)


def _window(year=2025, month=10, first=10, last=20, name='Durga Puja'):
    return FestivalWindow(name, pd.Timestamp(year=year, month=month, day=first),
                          pd.Timestamp(year=year, month=month, day=last))


def _plan(sales=None, window=None, cost_share=0.6, today=TODAY):
    sales = _history() if sales is None else sales
    return build_cash_plan(sales, window or _window(), today, LEAD_WEEKS, cost_share)


# --- the expected figure ----------------------------------------------------

def test_expected_sales_are_the_recent_baseline_times_the_usual_lift_times_the_days():
    plan = _plan()
    assert isinstance(plan, CashPlan)
    assert plan.festival == 'Durga Puja'
    assert plan.expected == pytest.approx(DAILY * 3 * 11)


def test_the_lift_counts_how_many_past_festivals_it_rests_on():
    assert _plan().lift_windows == 6  # 2019 to 2024


def test_a_festival_with_fewer_than_two_past_windows_gets_no_plan():
    sales = _history(festivals=FESTIVALS[2:3], first_year=2024)
    assert _plan(sales) is None


def test_a_festival_never_seen_before_gets_no_plan():
    assert _plan(window=_window(name='Never Seen', month=9, first=1, last=9)) is None


def test_no_sales_before_today_gives_no_plan():
    assert _plan(_history().iloc[0:0]) is None


def test_stale_data_gives_no_plan():
    sales = _history()
    assert _plan(sales[sales['date'] < TODAY - 20 * DAY]) is None


def test_a_thin_baseline_gives_no_plan():
    sales = _history()
    # this year only, the 91 days before today are all festival days but one
    for label, first, last in (('X1', '2025-05-21', '2025-06-30'),
                               ('X2', '2025-07-01', '2025-07-31'),
                               ('X3', '2025-08-01', '2025-08-18')):
        sales.loc[(sales['date'] >= first) & (sales['date'] <= last), 'festival'] = label
    assert _plan(sales) is None


def test_data_after_today_is_never_used():
    sales = _history()
    future = make_sales([{'date': TODAY + k * DAY, 'price': 99999.0, 'festival': None}
                         for k in range(5)])
    assert _plan(pd.concat([sales, future])).expected == pytest.approx(_plan(sales).expected)


# --- the range --------------------------------------------------------------

def test_steady_history_gives_a_narrow_range():
    plan = _plan()
    assert plan.low == pytest.approx(plan.expected)
    assert plan.high == pytest.approx(plan.expected)
    assert plan.backtest_windows >= MIN_BACKTEST_WINDOWS


def test_uneven_history_gives_a_wider_range():
    uneven = _history(boost=lambda year: 2 + (year % 3))
    steady, wide = _plan(), _plan(uneven)
    assert wide.high - wide.low > steady.high - steady.low


def test_the_range_is_ordered():
    plan = _plan(_history(boost=lambda year: 2 + (year % 3)))
    assert 0 < plan.low <= plan.high


def test_too_few_backtests_gives_an_expected_figure_but_no_range():
    sales = _history(festivals=FESTIVALS[2:3], first_year=2021)
    plan = _plan(sales)
    assert plan.expected > 0
    assert plan.low is None and plan.high is None
    assert plan.backtest_windows < MIN_BACKTEST_WINDOWS


# --- the stock budget -------------------------------------------------------

def test_the_budget_is_the_sales_times_the_cost_share():
    plan = _plan(_history(boost=lambda year: 2 + (year % 3)), cost_share=0.6)
    assert plan.budget_expected == pytest.approx(plan.expected * 0.6)
    assert plan.budget_low == pytest.approx(plan.low * 0.6)


def test_no_cost_share_means_no_budget():
    plan = _plan(cost_share=None)
    assert plan.budget_expected is None and plan.budget_low is None
    assert plan.expected > 0


def test_no_range_means_no_low_budget():
    plan = _plan(_history(festivals=FESTIVALS[2:3], first_year=2021))
    assert plan.budget_low is None and plan.budget_expected is not None


# --- lockdown windows are left out ------------------------------------------

def test_a_lockdown_window_does_not_count_towards_the_lift():
    lockdown = (pd.Timestamp('2020-09-25'), pd.Timestamp('2020-11-05'))
    assert _plan(_history(lockdown=lockdown)).lift_windows == 5  # 2020 left out
