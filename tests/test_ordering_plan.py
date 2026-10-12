import pandas as pd
import pytest
from test_ordering_forecast import LEAD_WEEKS, TODAY, _history, _window

from src.metrics.ordering_plan import OrderPlan, build_order_plan

RESALE = 0.5  # leftovers resell for half the price: ratio 0.8 at 100 / 60


def _stock(**rows):
    """A stock sheet: product -> (on hand, on order, minimum order)."""
    return pd.DataFrame([
        {'product': name, 'stock_on_hand': held, 'count_date': TODAY - pd.Timedelta(days=1),
         'weeks_to_arrive': LEAD_WEEKS, 'min_order_qty': least, 'on_order': coming}
        for name, (held, coming, least) in rows.items()])


def _plan(sales=None, stock=None, budget=None, resale=RESALE, window=None):
    sales = _history() if sales is None else sales
    return build_order_plan(sales, stock, window or _window(), TODAY, LEAD_WEEKS,
                            resale, budget)


def _lines(plan):
    return {line.product: line for line in plan.lines}


# --- quantities -------------------------------------------------------------

def test_without_a_stock_sheet_the_plan_is_the_whole_need_and_says_so():
    plan = _plan()
    assert isinstance(plan, OrderPlan)
    lines = _lines(plan)
    assert (lines['A'].to_order, lines['B'].to_order) == (33, 66)
    assert plan.stock_known is False
    assert plan.total_cost == pytest.approx(99 * 60.0)


def test_stock_on_hand_and_on_order_come_off_the_need():
    plan = _plan(stock=_stock(A=(20, 5, 1), B=(0, 0, 1)))
    assert plan.stock_known is True
    assert _lines(plan)['A'].to_order == 33 - 20 - 5


def test_the_minimum_order_is_respected():
    plan = _plan(stock=_stock(A=(20, 5, 10), B=(0, 0, 1)))
    assert _lines(plan)['A'].to_order == 10


def test_a_product_already_covered_orders_nothing():
    plan = _plan(stock=_stock(A=(40, 0, 10), B=(0, 0, 1)))
    assert _lines(plan)['A'].to_order == 0


def test_a_product_sold_below_cost_is_not_ordered_for_the_festival():
    plan = _plan(sales=_history().assign(unit_price=50.0))
    assert all(line.to_order == 0 for line in plan.lines)


def test_each_line_says_how_likely_it_is_to_sell_out_and_what_is_left_over():
    line = _lines(_plan())['A']
    assert line.sellout_chance == pytest.approx(1.0)  # steady history: demand = forecast
    assert line.expected_leftover == pytest.approx(0.0)


# --- the budget -------------------------------------------------------------

def test_a_budget_that_is_too_small_trims_the_order_and_says_so():
    plan = _plan(budget=3000.0)
    assert plan.trimmed is True
    assert plan.total_cost <= 3000.0


def test_a_budget_that_covers_the_plan_changes_nothing():
    plan = _plan(budget=10000.0)
    assert plan.trimmed is False
    assert _lines(plan)['B'].to_order == 66


def test_trimming_cannot_leave_an_order_below_the_minimum():
    plan = _plan(stock=_stock(A=(0, 0, 20), B=(0, 0, 20)), budget=3000.0)
    assert all(line.to_order == 0 or line.to_order >= 20 for line in plan.lines)
    assert plan.total_cost <= 3000.0


# --- when there is nothing to say -------------------------------------------

def test_too_little_history_gives_no_plan():
    short = _history(first_year=2024)
    assert _plan(sales=short, window=_window()) is None


def test_a_resale_share_outside_zero_to_one_is_refused():
    with pytest.raises(ValueError):
        _plan(resale=1.5)
