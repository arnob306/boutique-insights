from datetime import date

import pandas as pd
import pytest

from src.decisions.evaluate import evaluate_due, evaluate_log, summarise
from src.decisions.store import (
    DecisionLogError,
    Outcome,
    Recommendation,
    add_outcome,
    add_recommendations,
    list_outcomes,
    list_pending,
    open_log,
)

AS_OF = date(2026, 9, 6)
EVALUATED_ON = date(2026, 10, 1)


def make_rec(**overrides):
    fields = dict(
        as_of=AS_OF, product='Silk Saree', kind='forecast', model='velocity-v1',
        horizon_weeks=2, suggested_qty=0, expected_demand=6.0, confidence='good',
    )
    return Recommendation(**{**fields, **overrides})


def sales_frame(rows):
    frame = pd.DataFrame(rows, columns=['date', 'product', 'quantity'])
    return frame.assign(date=pd.to_datetime(frame['date']))


# The two-week window after 2026-09-06 is 07 Sep to 20 Sep inclusive.
SALES = sales_frame([
    ('2026-09-06', 'Silk Saree', 5),   # on the recommendation day: not in the window
    ('2026-09-07', 'Silk Saree', 2),   # first day of the window
    ('2026-09-10', 'Silk Saree', -1),  # a refund is not demand
    ('2026-09-20', 'Silk Saree', 3),   # last day of the window
    ('2026-09-21', 'Silk Saree', 9),   # after the window
    ('2026-09-10', 'Potli Bag', 4),    # a different product
])


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / 'decisions.sqlite'


def test_units_sold_counts_only_this_product_inside_the_window(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec()])
        assert evaluate_due(conn, SALES, EVALUATED_ON) == 1
        (outcome,) = list_outcomes(conn)
    assert outcome.units_sold == 5


def test_forecast_error_is_expected_minus_actual(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec(expected_demand=6.0)])
        evaluate_due(conn, SALES, EVALUATED_ON)
        (outcome,) = list_outcomes(conn)
    assert outcome.forecast_error == pytest.approx(1.0)  # over-forecast by one unit


def test_outcome_records_when_it_was_evaluated(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec()])
        evaluate_due(conn, SALES, EVALUATED_ON)
        (outcome,) = list_outcomes(conn)
    assert outcome.evaluated_on == EVALUATED_ON


def test_a_window_that_has_not_finished_is_left_pending(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec(horizon_weeks=4)])  # ends 04 Oct
        assert evaluate_due(conn, SALES, EVALUATED_ON) == 0
        assert len(list_pending(conn)) == 1
        assert list_outcomes(conn) == []


def test_evaluating_twice_does_not_duplicate_or_rewrite(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec()])
        evaluate_due(conn, SALES, EVALUATED_ON)
        later = sales_frame([('2026-09-08', 'Silk Saree', 100),
                             ('2026-10-30', 'Potli Bag', 1)])
        assert evaluate_due(conn, later, date(2026, 11, 1)) == 0
        (outcome,) = list_outcomes(conn)
    assert outcome.units_sold == 5
    assert outcome.evaluated_on == EVALUATED_ON


def test_evaluated_recommendations_are_no_longer_pending(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec(), make_rec(product='Potli Bag')])
        evaluate_due(conn, SALES, EVALUATED_ON)
        assert list_pending(conn) == []


def test_a_product_with_no_sales_in_the_window_sold_zero(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec(product='Bindi Pack', expected_demand=2.0)])
        evaluate_due(conn, SALES, EVALUATED_ON)
        (outcome,) = list_outcomes(conn)
    assert outcome.units_sold == 0
    assert outcome.forecast_error == pytest.approx(2.0)


def test_empty_sales_evaluate_nothing(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec()])
        assert evaluate_due(conn, sales_frame([]), EVALUATED_ON) == 0


def test_manual_fields_start_empty(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec()])
        evaluate_due(conn, SALES, EVALUATED_ON)
        (outcome,) = list_outcomes(conn)
    assert outcome.leftover_units is None
    assert outcome.stocked_out is None


def test_add_outcome_for_unknown_recommendation_is_refused(log_path):
    with open_log(log_path) as conn:
        with pytest.raises(DecisionLogError, match='recommendation'):
            add_outcome(conn, Outcome(
                recommendation_id=42, evaluated_on=EVALUATED_ON,
                units_sold=1, forecast_error=0.0))


def test_add_outcome_reports_whether_it_was_new(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec()])
        (rec,) = list_pending(conn)
        outcome = Outcome(recommendation_id=rec.id, evaluated_on=EVALUATED_ON,
                          units_sold=5, forecast_error=1.0)
        assert add_outcome(conn, outcome) is True
        assert add_outcome(conn, outcome) is False


def test_negative_units_sold_is_rejected():
    with pytest.raises(DecisionLogError):
        Outcome(recommendation_id=1, evaluated_on=EVALUATED_ON,
                units_sold=-1, forecast_error=0.0)


def test_summary_gives_wape_and_bias_per_model(log_path):
    sales = sales_frame([
        ('2026-09-08', 'Silk Saree', 5),
        ('2026-09-08', 'Potli Bag', 4),
        ('2026-09-30', 'Silk Saree', 1),
    ])
    with open_log(log_path) as conn:
        add_recommendations(conn, [
            make_rec(expected_demand=6.0),                        # error +1, sold 5
            make_rec(product='Potli Bag', expected_demand=2.0),   # error -2, sold 4
        ])
        evaluate_due(conn, sales, EVALUATED_ON)
        (row,) = summarise(conn)
    assert (row.model, row.kind, row.n) == ('velocity-v1', 'forecast', 2)
    assert row.wape == pytest.approx(3 / 9)    # (|1| + |-2|) / (5 + 4)
    assert row.bias == pytest.approx(-0.5)     # (1 - 2) / 2


def test_summary_is_empty_before_anything_is_evaluated(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec()])
        assert summarise(conn) == []


def test_evaluate_log_scores_a_log_file_and_returns_the_count(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [
            make_rec(),                                      # window over: scored
            make_rec(product='Potli Bag', horizon_weeks=4),  # window still open
        ])
    assert evaluate_log(SALES, log_path, EVALUATED_ON) == 1
    with open_log(log_path) as conn:
        assert len(list_outcomes(conn)) == 1
        assert len(list_pending(conn)) == 1


def test_wape_is_undefined_when_nothing_sold(log_path):
    with open_log(log_path) as conn:
        add_recommendations(conn, [make_rec(product='Bindi Pack', expected_demand=2.0)])
        evaluate_due(conn, SALES, EVALUATED_ON)
        (row,) = summarise(conn)
    assert row.wape is None
