from dataclasses import replace

import pytest

from src.adapters.boutique_xlsx import load_workbook_data
from src.decisions.record import (
    FORECAST_HORIZON_WEEKS,
    FORECAST_MODEL,
    REORDER_MODEL,
    build_recommendations,
    record_week,
)
from src.decisions.store import list_recommendations, open_log
from src.metrics.patterns import festival_uplift
from src.metrics.reorder import reorder_suggestions
from src.metrics.velocity import weekly_velocity

SALT = 'unit-test-salt-0123456789'


@pytest.fixture(scope='module')
def data(workbook_path):
    return load_workbook_data(workbook_path, salt=SALT)


@pytest.fixture(scope='module')
def as_of(data):
    return data.sales['date'].max().normalize()


def _of_kind(recs, kind):
    return [r for r in recs if r.kind == kind]


def test_recommendations_are_dated_by_the_newest_sale_not_today(data, as_of):
    recs = build_recommendations(data)
    assert recs
    assert {r.as_of for r in recs} == {as_of.date()}


def test_one_forecast_per_product_using_recent_velocity(data, as_of):
    recs = _of_kind(build_recommendations(data), 'forecast')
    velocity = weekly_velocity(data.sales, as_of)
    by_product = {r.product: r for r in recs}
    assert set(by_product) == set(velocity.dropna(subset=['units_per_week']).index)
    sample = next(iter(by_product.values()))
    expected = velocity.loc[sample.product, 'units_per_week'] * FORECAST_HORIZON_WEEKS
    assert sample.expected_demand == pytest.approx(expected)
    assert (sample.model, sample.horizon_weeks, sample.suggested_qty) == (
        FORECAST_MODEL, FORECAST_HORIZON_WEEKS, 0)


def test_reorder_rows_mirror_the_reorder_suggestions(data, as_of):
    recs = _of_kind(build_recommendations(data), 'reorder')
    suggestions = reorder_suggestions(
        data.sales, data.stock, as_of, calendar=[], uplift=festival_uplift(data.sales))
    assert len(recs) == len(suggestions)
    by_product = {r.product: r for r in recs}
    for row in suggestions.itertuples():
        rec = by_product[row.product]
        assert rec.suggested_qty == row.order_qty
        assert rec.expected_demand == pytest.approx(row.expected_demand)
        assert rec.horizon_weeks == row.lead_weeks + 1
        assert (rec.model, rec.confidence) == (REORDER_MODEL, row.confidence)


def test_decisions_not_to_order_are_logged_too(data):
    recs = _of_kind(build_recommendations(data), 'reorder')
    assert any(r.suggested_qty == 0 for r in recs)


def test_without_a_stock_sheet_only_forecasts_are_logged(data):
    recs = build_recommendations(replace(data, stock=None))
    assert recs
    assert {r.kind for r in recs} == {'forecast'}


def test_record_week_writes_the_log_and_returns_the_count(data, tmp_path):
    path = tmp_path / 'private' / 'decisions.sqlite'
    added = record_week(data, path)
    with open_log(path) as conn:
        stored = list_recommendations(conn)
    assert added == len(stored) == len(build_recommendations(data))


def test_recording_the_same_week_twice_adds_nothing(data, tmp_path):
    path = tmp_path / 'decisions.sqlite'
    record_week(data, path)
    assert record_week(data, path) == 0
