import numpy as np
import pandas as pd
import pytest
from test_metrics import make_sales

from src.metrics.calendar import FestivalWindow
from src.metrics.ordering_forecast import (
    MIN_SPREAD_SAMPLES,
    spread_cases,
    spread_ratios,
    unit_forecasts,
)

DAY = pd.Timedelta(days=1)
TODAY = pd.Timestamp('2025-08-20')
LEAD_WEEKS = 6

# (name, month, first day, last day)
FESTIVALS = (('Saraswati Puja', 2, 1, 11), ('Eid', 3, 3, 11),
             ('Durga Puja', 10, 10, 20), ('Christmas', 12, 12, 25))
# (product, category, units on an ordinary day)
PRODUCTS = (('A', 'Saree', 1), ('B', 'Saree', 2))


def _history(products=PRODUCTS, festivals=FESTIVALS, first_year=2019,
             last_day=TODAY - DAY, boost=lambda year, name: 3, extra=()):
    """Each product sells a steady daily amount, ``boost`` times that in a festival."""
    windows = [(name, pd.Timestamp(year=year, month=month, day=first),
                pd.Timestamp(year=year, month=month, day=last))
               for name, month, first, last in festivals
               for year in range(first_year, last_day.year + 1)]
    rows = list(extra)
    for date in pd.date_range(f'{first_year}-01-01', last_day):
        label = next((name for name, a, b in windows if a <= date <= b), None)
        times = boost(date.year, label) if label else 1
        for product, category, per_day in products:
            rows += [{'date': date, 'product': product, 'category': category,
                      'festival': label, 'price': 100.0, 'cost': 60.0}] * (per_day * times)
    return make_sales(rows)


def _window(year=2025, month=10, first=10, last=20, name='Durga Puja'):
    return FestivalWindow(name, pd.Timestamp(year=year, month=month, day=first),
                          pd.Timestamp(year=year, month=month, day=last))


def _forecast(sales=None, window=None, today=TODAY):
    sales = _history() if sales is None else sales
    return unit_forecasts(sales, window or _window(), today, LEAD_WEEKS)


# --- expected units per product ---------------------------------------------

def test_each_products_expected_units_are_its_baseline_times_the_usual_lift_times_the_days():
    forecast = _forecast()
    assert forecast['A'] == pytest.approx(1 * 3 * 11)
    assert forecast['B'] == pytest.approx(2 * 3 * 11)


def test_a_festival_with_fewer_than_two_past_windows_has_no_forecast():
    sales = _history(festivals=FESTIVALS[2:3], first_year=2024)
    assert _forecast(sales) == {}


def test_a_product_with_no_recent_sales_is_left_out():
    quiet = _history(products=PRODUCTS + (('C', 'Saree', 0),))
    assert 'C' not in _forecast(quiet)


def test_the_products_own_stock_out_days_do_not_drag_its_baseline_down():
    gone = pd.Timestamp('2025-06-10')
    back = pd.Timestamp('2025-07-20')
    marks = [{'date': gone, 'product': 'A', 'category': 'Saree',
              'event': 'A out of stock', 'price': 100.0, 'cost': 60.0},
             {'date': back, 'product': 'A', 'category': 'Saree',
              'event': 'A out of stock', 'price': 100.0, 'cost': 60.0}]
    sales = _history(extra=marks)
    sales = sales[~((sales['product'] == 'A') & (sales['date'] > gone)
                    & (sales['date'] < back))]
    assert _forecast(sales)['A'] == pytest.approx(1 * 3 * 11, rel=0.05)


# --- the spread from past festivals -----------------------------------------

def test_a_steady_history_has_no_spread():
    ratios = spread_ratios(_history(), TODAY, LEAD_WEEKS)
    assert len(ratios) > MIN_SPREAD_SAMPLES
    assert np.allclose(ratios, 1.0)


def test_a_surprise_festival_shows_up_as_actual_over_forecast():
    def boost(year, name):
        return 6 if (year == 2024 and name == 'Durga Puja') else 3

    ratios = spread_ratios(_history(boost=boost), TODAY, LEAD_WEEKS)
    assert sorted(np.round(ratios[ratios > 1.5], 6)) == [2.0, 2.0]  # A and B
    assert np.allclose(ratios[ratios <= 1.5], 1.0)  # earlier windows unchanged


def test_each_spread_case_remembers_how_big_its_forecast_was():
    forecasts, ratios = spread_cases(_history(), TODAY, LEAD_WEEKS)
    assert len(forecasts) == len(ratios) > MIN_SPREAD_SAMPLES
    # A sells 3 a day and B 6 a day in a festival of 9, 11 or 14 days.
    assert set(np.round(forecasts)) == {27.0, 33.0, 42.0, 54.0, 66.0, 84.0}
    assert np.allclose(ratios, 1.0)


def test_too_few_past_windows_give_no_spread_at_all():
    sales = _history(festivals=FESTIVALS[2:3], first_year=2022)
    assert len(spread_ratios(sales, TODAY, LEAD_WEEKS)) == 0
