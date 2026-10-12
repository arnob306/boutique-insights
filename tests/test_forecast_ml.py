import numpy as np
import pandas as pd
import pytest

from src.forecast.compare import run_comparison
from src.forecast.methods import velocity_rule
from src.forecast.ml import (
    FEATURES,
    PANEL_METHODS,
    gradient_boosting,
    training_rows,
)

HORIZON = 4
WEEKS = 160


def _starts(weeks=WEEKS):
    return pd.date_range('2020-01-06', periods=weeks, freq='7D')


def _ramp(weeks=WEEKS):
    """One product selling 0, 1, 2, ... units: every feature is easy to check."""
    return np.arange(weeks, dtype=float).reshape(-1, 1), np.ones((weeks, 1), dtype=bool)


def _demand(weeks=WEEKS, seed=0):
    rng = np.random.default_rng(seed)
    season = 1 + 0.5 * np.sin(2 * np.pi * np.arange(weeks) / 52)
    counts = rng.poisson(np.outer(season, [2.0, 5.0, 0.7]))
    return pd.DataFrame(counts, index=_starts(weeks), columns=['A', 'B', 'C'])


# --- the training rows ------------------------------------------------------

def test_a_rows_features_come_only_from_the_weeks_before_it():
    units, observed = _ramp()
    rows, targets = training_rows(units, observed, _starts(), HORIZON)
    row = rows.loc[(100, 0)]
    assert row['mean4'] == pytest.approx(units[96:100].mean())
    assert row['mean13'] == pytest.approx(units[87:100].mean())
    assert row['mean26'] == pytest.approx(units[74:100].mean())
    assert row['level52'] == pytest.approx(units[48:100].mean())


def test_a_rows_target_is_the_units_in_the_next_horizon_weeks():
    units, observed = _ramp()
    rows, targets = training_rows(units, observed, _starts(), HORIZON)
    position = list(rows.index).index((100, 0))
    assert targets[position] == pytest.approx(units[100:104].sum())


def test_last_years_units_are_the_same_weeks_a_year_earlier():
    units, observed = _ramp()
    rows, _ = training_rows(units, observed, _starts(), HORIZON)
    assert rows.loc[(100, 0), 'last_year'] == pytest.approx(units[48:52].sum())


def test_last_year_is_missing_when_there_is_no_year_of_history_yet():
    units, observed = _ramp()
    rows, _ = training_rows(units, observed, _starts(), HORIZON)
    assert np.isnan(rows.loc[(40, 0), 'last_year'])


def test_last_year_is_missing_when_those_weeks_were_not_normal_trading():
    units, observed = _ramp()
    observed[49, 0] = False
    rows, _ = training_rows(units, observed, _starts(), HORIZON)
    assert np.isnan(rows.loc[(100, 0), 'last_year'])


def test_a_target_that_overlaps_a_stock_out_is_not_used_for_training():
    units, observed = _ramp()
    observed[102, 0] = False
    rows, _ = training_rows(units, observed, _starts(), HORIZON)
    assert (99, 0) not in rows.index  # its target weeks 99 to 102 include week 102
    assert (103, 0) in rows.index


def test_the_features_are_named_in_one_place():
    units, observed = _ramp()
    rows, _ = training_rows(units, observed, _starts(), HORIZON)
    assert tuple(rows.columns) == FEATURES


# --- the forecast -----------------------------------------------------------

def test_it_forecasts_every_product_and_never_a_negative_amount():
    demand = _demand()
    forecast = gradient_boosting(
        demand.to_numpy(float), np.ones(demand.shape, dtype=bool), demand.index, HORIZON)
    assert forecast.shape == (3,)
    assert (forecast >= 0).all()


def test_steady_demand_is_forecast_close_to_itself():
    weeks = 200
    units = np.full((weeks, 2), 5.0)
    forecast = gradient_boosting(
        units, np.ones(units.shape, dtype=bool), _starts(weeks), HORIZON)
    assert forecast == pytest.approx([20.0, 20.0], rel=0.25)


def test_with_too_little_history_it_falls_back_to_the_live_rule():
    units = _demand(weeks=40).to_numpy(float)
    observed = np.ones(units.shape, dtype=bool)
    forecast = gradient_boosting(units, observed, _starts(40), HORIZON)
    expected = [velocity_rule(units[:, i], observed[:, i], HORIZON) for i in range(3)]
    assert forecast == pytest.approx(expected)


def test_the_same_history_gives_the_same_forecast():
    demand = _demand()
    args = (demand.to_numpy(float), np.ones(demand.shape, dtype=bool), demand.index, HORIZON)
    assert gradient_boosting(*args) == pytest.approx(gradient_boosting(*args))


# --- inside the rolling comparison ------------------------------------------

def _compare(demand):
    censored = pd.DataFrame(False, index=demand.index, columns=demand.columns)
    return run_comparison(demand, censored, methods={}, panel_methods=PANEL_METHODS,
                          horizon=HORIZON, min_train=104, step=8)


def test_the_comparison_scores_it_with_one_row_per_origin_and_product():
    demand = _demand()
    results = _compare(demand)
    assert set(results['method']) == {'gradient boosting'}
    origins = len(range(104, WEEKS - HORIZON + 1, 8))
    assert len(results) == origins * 3
    assert list(results.columns) == [
        'target_start', 'product', 'method', 'forecast', 'actual', 'scored']


def test_a_forecast_never_sees_the_weeks_it_is_checked_against():
    demand = _demand()
    changed = demand.copy()
    changed.iloc[104:108] = 50  # the first origin's target weeks
    first = demand.index[104]

    def forecasts(frame):
        results = _compare(frame)
        return results[results['target_start'] == first]['forecast'].to_numpy()

    assert forecasts(changed) == pytest.approx(forecasts(demand))
