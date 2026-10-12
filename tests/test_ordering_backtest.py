import pytest
from test_ordering_forecast import LEAD_WEEKS, _history

from src.metrics.ordering import realised_profit
from src.metrics.ordering_backtest import BacktestResult, backtest_ordering

RESALE = 0.5
# Enough products that the early festivals still give a spread to read.
MANY = tuple((f'P{i}', 'Saree', 1 + i % 3) for i in range(8))


def _past(**kwargs):
    return _history(products=MANY, **kwargs)


def _scores(result):
    return {score.name: score for score in result.scores}


# --- scoring one case -------------------------------------------------------

def test_overstock_earns_the_margin_on_what_sold_and_loses_on_the_leftovers():
    # 10 ordered, 7 sold: 7 sales at 40 margin, 3 leftovers losing 10 each.
    assert realised_profit(10, 7, underage=40.0, overage=10.0) == pytest.approx(250.0)


def test_understock_earns_only_on_what_was_ordered():
    assert realised_profit(5, 7, underage=40.0, overage=10.0) == pytest.approx(200.0)


# --- the comparison ---------------------------------------------------------

def test_a_steady_history_scores_every_rule_the_same():
    result = backtest_ordering(_past(), LEAD_WEEKS, RESALE)
    assert isinstance(result, BacktestResult)
    assert result.cases > 0
    profits = {round(score.profit, 6) for name, score in _scores(result).items()
               if name != 'expected_plus_buffer'}
    assert len(profits) == 1


def test_the_rules_are_named_and_counted():
    result = backtest_ordering(_past(), LEAD_WEEKS, RESALE)
    assert set(_scores(result)) == {
        'newsvendor', 'newsvendor_by_size', 'expected', 'expected_plus_buffer'}
    assert result.windows > 0


def test_when_demand_swings_the_newsvendor_covers_more_of_it_than_the_plain_forecast():
    swinging = _past(boost=lambda year, name: 2 if year % 2 else 5)
    scores = _scores(backtest_ordering(swinging, LEAD_WEEKS, RESALE))
    assert scores['newsvendor'].fill_rate > scores['expected'].fill_rate
    assert scores['newsvendor'].missed_units < scores['expected'].missed_units


def test_an_ordering_rule_only_sees_data_from_before_its_order_date():
    # Changing the latest festival's sales cannot change what the earlier
    # festivals would have been told to order.
    def boost(year, name):
        return 3

    def surprise(year, name):
        return 9 if (year == 2024 and name == 'Christmas') else 3

    steady = backtest_ordering(_past(boost=boost), LEAD_WEEKS, RESALE)
    shocked = backtest_ordering(_past(boost=surprise), LEAD_WEEKS, RESALE)
    assert steady.cases > 0
    assert shocked.cases == steady.cases
    assert _scores(shocked)['expected'].spend == pytest.approx(
        _scores(steady)['expected'].spend, rel=0.05)


def test_too_little_history_gives_an_empty_comparison():
    result = backtest_ordering(_history(first_year=2024), LEAD_WEEKS, RESALE)
    assert result.cases == 0
