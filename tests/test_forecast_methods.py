import numpy as np
import pytest

from src.forecast.methods import (
    METHODS,
    recent_mean,
    seasonal_naive,
    velocity_rule,
)

HORIZON = 4


def seen(history):
    """Every week was a normal trading week."""
    return np.ones(len(history), dtype=bool)


def test_velocity_rule_projects_the_last_13_weeks_average():
    history = np.array([100] * 7 + [2] * 13)
    assert velocity_rule(history, seen(history), HORIZON) == pytest.approx(8.0)


def test_velocity_rule_skips_weeks_that_were_not_observed():
    history = np.array([2] * 10 + [0] * 3)
    observed = np.array([True] * 10 + [False] * 3)  # zeros came from a stock-out
    assert velocity_rule(history, observed, HORIZON) == pytest.approx(8.0)


def test_velocity_rule_with_nothing_observed_forecasts_zero():
    history = np.array([5, 5, 5])
    assert velocity_rule(history, np.zeros(3, dtype=bool), HORIZON) == 0.0


def test_velocity_rule_uses_what_exists_when_history_is_short():
    history = np.array([3, 3])
    assert velocity_rule(history, seen(history), HORIZON) == pytest.approx(12.0)


def test_velocity_rule_with_no_history_forecasts_zero():
    empty = np.array([], dtype=int)
    assert velocity_rule(empty, seen(empty), HORIZON) == 0.0


def test_recent_mean_uses_only_the_last_8_weeks():
    history = np.array([100] * 10 + [1] * 8)
    assert recent_mean(history, seen(history), HORIZON) == pytest.approx(4.0)


def test_seasonal_naive_repeats_the_same_weeks_last_year():
    history = np.zeros(60, dtype=int)
    history[8:12] = [1, 2, 3, 4]  # weeks 60 to 63 line up with weeks 8 to 11
    assert seasonal_naive(history, seen(history), HORIZON) == pytest.approx(10.0)


def test_seasonal_naive_falls_back_to_recent_mean_without_a_full_year():
    history = np.array([2] * 40)
    assert seasonal_naive(history, seen(history), HORIZON) == recent_mean(
        history, seen(history), HORIZON)


def test_seasonal_naive_falls_back_when_last_years_weeks_were_censored():
    history = np.array([7] * 20 + [1] * 40)
    observed = seen(history)
    observed[9] = False  # one of the weeks 8 to 11 last year was a stock-out
    assert seasonal_naive(history, observed, HORIZON) == recent_mean(
        history, observed, HORIZON)


def test_seasonal_naive_falls_back_for_a_horizon_longer_than_a_season():
    history = np.arange(120)
    assert seasonal_naive(history, seen(history), 60) == recent_mean(
        history, seen(history), 60)


def test_methods_registry_names_all_three():
    assert set(METHODS) == {'velocity rule', 'recent mean', 'seasonal naive'}
    assert METHODS['velocity rule'] is velocity_rule
