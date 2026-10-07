import numpy as np
import pandas as pd
import pytest

from src.forecast.compare import (
    RESULT_COLUMNS,
    rolling_origins,
    run_comparison,
    score,
    score_by_year,
)
from src.forecast.methods import METHODS

HORIZON = 2


def weeks(count, start='2024-01-01'):
    return pd.date_range(start, periods=count, freq='7D', name='week_start')


def make_demand(values, product='A'):
    values = list(values)
    return pd.DataFrame({product: values}, index=weeks(len(values)))


def no_censoring(demand):
    return pd.DataFrame(False, index=demand.index, columns=demand.columns)


def last_week(history, observed, horizon):
    return float(history[-1]) * horizon


def always_zero(history, observed, horizon):
    return 0.0


STUBS = {'last week': last_week, 'zero': always_zero}


def run(demand, censored=None, methods=None, **kwargs):
    kwargs = {'horizon': HORIZON, 'min_train': 3, 'step': 2, **kwargs}
    return run_comparison(
        demand, no_censoring(demand) if censored is None else censored,
        STUBS if methods is None else methods, **kwargs)


def pick(results, method, target_start):
    """The single result row for one method and one forecast date."""
    rows = results[(results['method'] == method)
                   & (results['target_start'] == target_start)]
    assert len(rows) == 1
    return rows.iloc[0]


def test_origins_start_after_the_minimum_and_leave_room_for_the_horizon():
    assert rolling_origins(10, horizon=2, min_train=3, step=2) == [3, 5, 7]


def test_an_origin_may_use_the_very_last_weeks_as_its_target():
    assert rolling_origins(9, horizon=2, min_train=3, step=2) == [3, 5, 7]
    assert rolling_origins(8, horizon=2, min_train=3, step=2) == [3, 5]


def test_too_little_history_means_no_origins():
    assert rolling_origins(4, horizon=2, min_train=3, step=1) == []


@pytest.mark.parametrize('kwargs', [
    {'horizon': 0}, {'min_train': 0}, {'step': 0},
])
def test_nonsense_settings_are_refused(kwargs):
    demand = make_demand(range(10))
    with pytest.raises(ValueError):
        run(demand, **kwargs)


def test_a_mask_of_the_wrong_shape_is_refused():
    demand = make_demand(range(10))
    with pytest.raises(ValueError, match='shape'):
        run(demand, censored=no_censoring(demand).iloc[:5])


def test_one_row_per_origin_product_and_method():
    demand = make_demand(range(10))
    results = run(demand)
    assert list(results.columns) == RESULT_COLUMNS
    assert len(results) == 3 * 1 * 2
    assert set(results['method']) == {'last week', 'zero'}


def test_each_row_is_labelled_by_the_first_week_being_forecast():
    demand = make_demand(range(10))
    results = run(demand)
    assert sorted(set(results['target_start'])) == [
        demand.index[3], demand.index[5], demand.index[7]]


def test_actual_is_the_demand_over_the_horizon_weeks():
    demand = make_demand([1, 1, 1, 4, 5, 9, 9, 9, 9, 9])
    row = pick(run(demand), 'zero', demand.index[3])
    assert row['actual'] == 9  # weeks 3 and 4: 4 + 5


def test_a_method_only_sees_weeks_before_the_origin():
    demand = make_demand([1, 1, 7, 100, 100, 100, 100, 100, 100, 100])
    row = pick(run(demand), 'last week', demand.index[3])
    assert row['forecast'] == 14.0  # week 2 was 7, times two weeks


def test_a_target_overlapping_a_censored_week_is_not_scored():
    demand = make_demand(range(10))
    censored = no_censoring(demand)
    censored.iloc[4, 0] = True  # week 4 is inside the target of the origin at week 3
    results = run(demand, censored=censored)
    assert not pick(results, 'zero', demand.index[3])['scored']
    assert pick(results, 'zero', demand.index[5])['scored']
    assert pick(results, 'zero', demand.index[7])['scored']


def test_a_censored_history_week_is_passed_on_as_not_observed():
    seen = {}

    def spy(history, observed, horizon):
        seen[len(history)] = observed.copy()
        return 0.0

    demand = make_demand(range(10))
    censored = no_censoring(demand)
    censored.iloc[1, 0] = True
    run(demand, censored=censored, methods={'spy': spy})
    assert seen[3].tolist() == [True, False, True]


def test_too_short_a_history_gives_an_empty_table_with_the_right_columns():
    demand = make_demand(range(4))
    results = run(demand, min_train=3, step=1, horizon=3)
    assert results.empty
    assert list(results.columns) == RESULT_COLUMNS


def test_forecasts_never_depend_on_what_happens_after_the_origin():
    rng = np.random.default_rng(3)
    demand = pd.DataFrame(
        rng.poisson(2, size=(120, 2)), index=weeks(120), columns=['A', 'B'])
    censored = no_censoring(demand)
    cut = 90

    changed = demand.copy()
    changed.iloc[cut:] += 1000
    changed_censored = censored.copy()
    changed_censored.iloc[cut:] = True

    kwargs = dict(methods=METHODS, horizon=4, min_train=60, step=4)
    before = run(demand, censored=censored, **kwargs)
    after = run(changed, censored=changed_censored, **kwargs)

    key = ['target_start', 'product', 'method']
    known = before['target_start'] <= demand.index[cut]
    merged = before[known].merge(after, on=key, suffixes=('_before', '_after'))
    assert len(merged) == known.sum()
    assert len(merged) > 0
    assert (merged['forecast_before'] == merged['forecast_after']).all()
    # Sanity: the perturbation really did change what happened later.
    assert after['actual'].sum() > before['actual'].sum()


def table(*rows):
    return pd.DataFrame(rows, columns=RESULT_COLUMNS)


def test_wape_is_total_error_over_total_units_sold():
    results = table(
        (pd.Timestamp('2025-01-06'), 'A', 'm', 6.0, 5, True),
        (pd.Timestamp('2025-02-03'), 'A', 'm', 2.0, 4, True),
        (pd.Timestamp('2025-03-03'), 'A', 'm', 9.0, 1, False),  # not scored
    )
    board = score(results)
    assert board.loc['m', 'wape'] == pytest.approx(3 / 9)
    assert board.loc['m', 'bias'] == pytest.approx(-0.5)
    assert board.loc['m', 'n'] == 2


def test_wape_is_undefined_when_nothing_was_sold():
    results = table((pd.Timestamp('2025-01-06'), 'A', 'm', 3.0, 0, True))
    assert np.isnan(score(results).loc['m', 'wape'])


def test_scoring_an_empty_table_gives_an_empty_board():
    assert score(table()).empty


def test_scoreboard_is_sorted_best_first():
    results = table(
        (pd.Timestamp('2025-01-06'), 'A', 'bad', 9.0, 5, True),
        (pd.Timestamp('2025-01-06'), 'A', 'good', 5.0, 5, True),
    )
    assert list(score(results).index) == ['good', 'bad']


def test_by_year_columns_follow_the_scoreboard_order_best_first():
    results = table(
        (pd.Timestamp('2025-01-06'), 'A', 'a worse', 9.0, 5, True),
        (pd.Timestamp('2025-01-06'), 'A', 'z better', 5.0, 5, True),
    )
    assert list(score_by_year(results).columns) == ['z better', 'a worse']


def test_scoring_by_year_with_nothing_scored_gives_an_empty_table():
    results = table((pd.Timestamp('2025-01-06'), 'A', 'm', 3.0, 5, False))
    assert score_by_year(results).empty


def test_wape_by_year_has_one_row_per_year_and_one_column_per_method():
    results = table(
        (pd.Timestamp('2024-06-03'), 'A', 'm', 6.0, 5, True),
        (pd.Timestamp('2025-06-02'), 'A', 'm', 2.0, 4, True),
        (pd.Timestamp('2025-06-09'), 'A', 'm', 4.0, 4, True),
    )
    by_year = score_by_year(results)
    assert list(by_year.index) == [2024, 2025]
    assert by_year.loc[2024, 'm'] == pytest.approx(1 / 5)
    assert by_year.loc[2025, 'm'] == pytest.approx(2 / 8)
