import numpy as np
import pytest

from src.metrics.ordering import (
    MAX_RATIO,
    Candidate,
    critical_ratio,
    expected_leftover,
    order_quantity,
    sellout_chance,
    similar_cases,
    trim_to_budget,
)

# --- the critical ratio -----------------------------------------------------

def test_the_ratio_is_the_margin_over_the_margin_plus_the_leftover_loss():
    # Sells at 100, costs 60, leftovers resell for half the price (50):
    # a missed sale loses 40, a leftover loses 10, so order for an 80% chance.
    assert critical_ratio(100.0, 60.0, 0.5) == pytest.approx(0.8)


def test_a_product_sold_at_or_below_cost_is_not_worth_stocking_for_a_festival():
    assert critical_ratio(60.0, 60.0, 0.5) == 0.0
    assert critical_ratio(50.0, 60.0, 0.5) == 0.0


def test_leftovers_that_resell_above_cost_are_capped_not_unlimited():
    assert critical_ratio(100.0, 60.0, 0.8) == MAX_RATIO


def test_a_resale_share_outside_zero_to_one_is_refused():
    with pytest.raises(ValueError):
        critical_ratio(100.0, 60.0, 1.5)
    with pytest.raises(ValueError):
        critical_ratio(100.0, 60.0, -0.1)


# --- the quantity -----------------------------------------------------------

def test_the_quantity_is_the_demand_level_the_ratio_asks_for():
    ratios = np.linspace(0, 2, 101)  # demand from 0x to 2x the forecast
    assert order_quantity(10.0, ratios, 0.8) == 16


def test_the_quantity_rounds_up_to_whole_items():
    assert order_quantity(1.0, np.array([0.0, 1.0, 2.0, 3.0]), 0.5) == 2


def test_a_zero_ratio_orders_nothing():
    assert order_quantity(10.0, np.array([1.0, 2.0]), 0.0) == 0


def test_no_spread_data_gives_no_quantity():
    assert order_quantity(10.0, np.array([]), 0.8) is None


def test_the_sell_out_chance_is_the_share_of_cases_that_use_up_the_stock():
    scenarios = np.array([0.0, 1.0, 2.0, 3.0])
    assert sellout_chance(2, scenarios) == pytest.approx(0.5)


def test_expected_leftover_is_the_average_unsold_amount():
    scenarios = np.array([0.0, 1.0, 2.0, 3.0])
    assert expected_leftover(2, scenarios) == pytest.approx(0.75)


# --- reading the spread from similar-sized forecasts -------------------------

SIZES = np.array([1.0, 1.0, 1.0, 10.0, 10.0, 10.0])
SPREADS = np.array([3.0, 3.0, 3.0, 0.5, 0.5, 0.5])


def test_a_small_forecast_reads_the_spread_of_small_forecasts():
    assert list(similar_cases(1.2, SIZES, SPREADS, 3)) == [3.0, 3.0, 3.0]


def test_a_large_forecast_reads_the_spread_of_large_forecasts():
    assert list(similar_cases(9.0, SIZES, SPREADS, 3)) == [0.5, 0.5, 0.5]


def test_asking_for_more_cases_than_exist_returns_them_all():
    assert len(similar_cases(5.0, SIZES, SPREADS, 100)) == 6


def test_no_cases_gives_no_spread():
    assert len(similar_cases(5.0, np.array([]), np.array([]), 10)) == 0


# --- the budget -------------------------------------------------------------

def _candidate(product, quantity, cost, price, scenarios):
    return Candidate(product=product, quantity=quantity, unit_cost=cost,
                     underage=price - cost, overage=cost * 0.5,
                     scenarios=np.array(scenarios, dtype=float))


SPREAD = [2.0, 4.0, 6.0, 8.0]


def test_a_budget_that_covers_everything_changes_nothing():
    items = [_candidate('A', 6, 10.0, 30.0, SPREAD)]
    assert trim_to_budget(items, 1000.0) == {'A': 6}


def test_no_budget_changes_nothing():
    items = [_candidate('A', 6, 10.0, 30.0, SPREAD)]
    assert trim_to_budget(items, None) == {'A': 6}


def test_trimming_never_goes_over_the_budget():
    items = [_candidate('A', 6, 10.0, 30.0, SPREAD),
             _candidate('B', 6, 20.0, 50.0, SPREAD)]
    kept = trim_to_budget(items, 100.0)
    assert 10.0 * kept['A'] + 20.0 * kept['B'] <= 100.0


def test_trimming_cuts_the_product_that_earns_least_per_dollar_first():
    # Same demand and cost, but A earns 5 on a sale and B earns 40.
    items = [_candidate('A', 6, 10.0, 15.0, SPREAD),
             _candidate('B', 6, 10.0, 50.0, SPREAD)]
    kept = trim_to_budget(items, 90.0)
    assert kept['B'] > kept['A']
    assert kept['A'] + kept['B'] == 9


def test_a_budget_of_zero_orders_nothing():
    items = [_candidate('A', 6, 10.0, 30.0, SPREAD)]
    assert trim_to_budget(items, 0.0) == {'A': 0}


def test_the_inputs_are_not_changed():
    items = [_candidate('A', 6, 10.0, 30.0, SPREAD)]
    trim_to_budget(items, 20.0)
    assert items[0].quantity == 6
