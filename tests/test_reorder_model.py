import sys

import numpy as np
import pandas as pd
import pytest
from test_metrics import AS_OF, make_sales, stock_frame, weekly_sales

from src.forecast.live import MODEL_BASIS, model_forecaster
from src.metrics.calendar import FestivalWindow
from src.metrics.reorder import reorder_suggestions

WEEKS = 170


def _row(result, product='P'):
    return result.set_index('product').loc[product]


def _long_sales(weeks=WEEKS, seed=0):
    """Three invented products selling most weeks, newest week ending on AS_OF."""
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(weeks):
        for product, rate in (('P', 3.0), ('Q', 5.0), ('R', 1.5)):
            qty = int(rng.poisson(rate))
            if qty:
                rows.append({'date': AS_OF - pd.Timedelta(days=7 * k + 1),
                             'product': product, 'qty': qty})
    return make_sales(rows)


class Recorder:
    """A stand-in forecaster that remembers which horizons it was asked for."""

    def __init__(self, forecasts):
        self.forecasts = forecasts
        self.horizons = []

    def __call__(self, horizon_weeks):
        self.horizons.append(horizon_weeks)
        return self.forecasts


# --- reorder uses a model forecast when it has one ---------------------------

def test_a_model_forecast_replaces_the_velocity_estimate():
    result = reorder_suggestions(
        weekly_sales(13), stock_frame(), AS_OF, forecaster=Recorder({'P': 10.0}))
    row = _row(result)
    assert row['expected_demand'] == pytest.approx(10.0)
    assert row['safety_stock'] == 4          # ceil(sqrt(10))
    assert row['order_qty'] == 14
    assert row['basis'] == 'model'


def test_the_festival_factor_is_not_applied_on_top_of_a_model_forecast():
    calendar = [FestivalWindow(
        'Durga Puja', AS_OF + pd.Timedelta(days=1), AS_OF + pd.Timedelta(days=35))]
    uplift = pd.DataFrame([{
        'festival': 'Durga Puja', 'category': 'Saree', 'uplift': 3.0, 'windows': 3}])
    row = _row(reorder_suggestions(
        weekly_sales(13), stock_frame(), AS_OF, calendar=calendar, uplift=uplift,
        forecaster=Recorder({'P': 10.0})))
    assert row['expected_demand'] == pytest.approx(10.0)
    assert 'Durga Puja' in row['note']       # still worth telling her about


def test_the_model_is_asked_for_lead_time_plus_a_week():
    recorder = Recorder({'P': 10.0})
    reorder_suggestions(weekly_sales(13), stock_frame(weeks=3), AS_OF, forecaster=recorder)
    assert set(recorder.horizons) == {4}


def test_each_horizon_is_forecast_once_however_many_products_share_it():
    stock = pd.concat([stock_frame(product='P'), stock_frame(product='Q')])
    sales = pd.concat([weekly_sales(13, product='P'), weekly_sales(13, product='Q')])
    recorder = Recorder({'P': 10.0, 'Q': 10.0})
    reorder_suggestions(sales, stock, AS_OF, forecaster=recorder)
    assert recorder.horizons == [5]


# --- and falls back to the live rule otherwise -------------------------------

def test_without_a_forecaster_the_rule_is_used_as_before():
    row = _row(reorder_suggestions(weekly_sales(13), stock_frame(), AS_OF))
    assert row['expected_demand'] == pytest.approx(5.0)
    assert row['basis'] == 'rule'


def test_a_forecaster_with_nothing_to_offer_leaves_the_rule_in_place():
    row = _row(reorder_suggestions(
        weekly_sales(13), stock_frame(), AS_OF, forecaster=Recorder(None)))
    assert row['expected_demand'] == pytest.approx(5.0)
    assert row['basis'] == 'rule'


def test_a_product_the_model_does_not_cover_uses_the_rule():
    row = _row(reorder_suggestions(
        weekly_sales(13), stock_frame(), AS_OF, forecaster=Recorder({'OTHER': 9.0})))
    assert row['expected_demand'] == pytest.approx(5.0)
    assert row['basis'] == 'rule'


def test_a_product_with_no_recent_sales_is_not_ordered_even_if_the_model_guesses():
    row = _row(reorder_suggestions(
        weekly_sales(13), stock_frame(product='Z'), AS_OF,
        forecaster=Recorder({'Z': 8.0})), 'Z')
    assert row['order_qty'] == 0
    assert row['note'] == 'no recent sales'


# --- the real forecaster ------------------------------------------------------

def test_the_real_forecaster_covers_every_product_with_enough_history():
    forecaster = model_forecaster(_long_sales(), AS_OF)
    assert forecaster is not None
    forecasts = forecaster(5)
    assert set(forecasts) == {'P', 'Q', 'R'}
    assert all(value >= 0 for value in forecasts.values())
    # sells about 3, 5 and 1.5 a week, so about 15, 25 and 7.5 over five weeks
    assert forecasts['Q'] > forecasts['P'] > forecasts['R']
    assert forecasts['Q'] == pytest.approx(25, rel=0.4)


def test_the_same_history_gives_the_same_forecast():
    first = model_forecaster(_long_sales(), AS_OF)(5)
    second = model_forecaster(_long_sales(), AS_OF)(5)
    assert first == second


def test_with_too_little_history_there_is_no_forecaster():
    assert model_forecaster(weekly_sales(13), AS_OF) is None


def test_it_cannot_see_sales_after_the_as_of_date():
    sales = _long_sales()
    later = make_sales([{'date': AS_OF + pd.Timedelta(days=3), 'product': 'P',
                         'qty': 500}])
    assert (model_forecaster(pd.concat([sales, later], ignore_index=True), AS_OF)(5)
            == model_forecaster(sales, AS_OF)(5))


def test_without_scikit_learn_there_is_no_forecaster(monkeypatch):
    monkeypatch.setitem(sys.modules, 'sklearn.ensemble', None)
    assert model_forecaster(_long_sales(), AS_OF) is None


def test_the_real_forecaster_plugs_into_the_reorder_advice():
    sales = _long_sales()
    stock = pd.concat([stock_frame(product=p) for p in ('P', 'Q', 'R')])
    result = reorder_suggestions(
        sales, stock, AS_OF, forecaster=model_forecaster(sales, AS_OF))
    assert set(result['basis']) == {MODEL_BASIS}
