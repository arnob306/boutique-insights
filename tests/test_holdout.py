import pandas as pd
import pytest

from src.holdout import (
    HOLD,
    MAX_SHARE,
    MIN_PER_ARM,
    SEND,
    Holdout,
    arm_for,
    compare,
    eligible_ids,
)

SALT = 'unit-test-salt-0123456789'
IDS = [f'{i:016x}' for i in range(2000)]


def arms(share=0.2, festival='Diwali', year=2026, salt=SALT):
    return [arm_for(i, festival, year, salt, share) for i in IDS]


# --- who is held back ------------------------------------------------------

def test_assignment_is_the_same_every_time():
    assert arms() == arms()


def test_about_the_chosen_share_is_held_back():
    held = arms(0.2).count(HOLD) / len(IDS)
    assert held == pytest.approx(0.2, abs=0.03)


def test_a_larger_share_holds_back_a_superset():
    small = {i for i, a in zip(IDS, arms(0.1), strict=True) if a == HOLD}
    large = {i for i, a in zip(IDS, arms(0.3), strict=True) if a == HOLD}
    assert small <= large


def test_each_festival_and_year_draws_its_own_groups():
    assert arms(festival='Diwali') != arms(festival='Durga Puja')
    assert arms(year=2026) != arms(year=2027)


def test_the_secret_decides_the_groups():
    assert arms(salt=SALT) != arms(salt='another-secret-0123456789')


def test_only_send_and_hold_exist():
    assert set(arms()) == {SEND, HOLD}


@pytest.mark.parametrize('share', [0, -0.1, MAX_SHARE + 0.01, 1, float('nan')])
def test_a_silly_share_is_refused(share):
    with pytest.raises(ValueError, match='share'):
        arm_for(IDS[0], 'Diwali', 2026, SALT, share)


def test_the_share_may_be_exactly_the_maximum():
    assert arm_for(IDS[0], 'Diwali', 2026, SALT, MAX_SHARE) in (SEND, HOLD)


@pytest.mark.parametrize('share', [0, MAX_SHARE + 0.1, -1])
def test_the_settings_object_refuses_a_silly_share_when_made(share):
    with pytest.raises(ValueError, match='share'):
        Holdout(share=share, salt=SALT)


def test_the_settings_object_assigns_like_arm_for():
    settings = Holdout(share=0.2, salt=SALT)
    assert settings.arm(IDS[3], 'Diwali', 2026) == arm_for(IDS[3], 'Diwali', 2026, SALT, 0.2)


# --- who was eligible ------------------------------------------------------

def sales(*rows):
    frame = pd.DataFrame(rows, columns=['date', 'customer_id', 'quantity'])
    return frame.assign(date=pd.to_datetime(frame['date']))


LAST_START, LAST_END = pd.Timestamp('2025-10-10'), pd.Timestamp('2025-10-21')
LISTED = pd.Timestamp('2026-09-15')


def test_last_years_festival_buyers_are_eligible():
    data = sales(('2025-10-15', 'a', 1), ('2025-10-10', 'b', 2), ('2025-10-21', 'c', 1))
    assert eligible_ids(data, LAST_START, LAST_END, LISTED) == {'a', 'b', 'c'}


def test_buyers_outside_last_years_window_are_not_eligible():
    data = sales(('2025-10-09', 'a', 1), ('2025-10-22', 'b', 1))
    assert eligible_ids(data, LAST_START, LAST_END, LISTED) == set()


def test_someone_who_shopped_in_the_last_four_weeks_is_left_out():
    data = sales(('2025-10-15', 'a', 1), ('2026-08-25', 'a', 1),   # 21 days before
                 ('2025-10-15', 'b', 1), ('2026-08-10', 'b', 1))   # 36 days before
    assert eligible_ids(data, LAST_START, LAST_END, LISTED) == {'b'}


def test_the_list_day_itself_counts_as_recent():
    data = sales(('2025-10-15', 'a', 1), ('2026-09-15', 'a', 1))
    assert eligible_ids(data, LAST_START, LAST_END, LISTED) == set()


def test_refunds_and_unnamed_sales_do_not_make_anyone_eligible():
    data = sales(('2025-10-15', 'a', -1), ('2025-10-15', None, 1))
    assert eligible_ids(data, LAST_START, LAST_END, LISTED) == set()


def test_later_purchases_do_not_change_who_was_eligible():
    before = sales(('2025-10-15', 'a', 1))
    after = sales(('2025-10-15', 'a', 1), ('2026-10-01', 'a', 1))
    assert (eligible_ids(before, LAST_START, LAST_END, LISTED)
            == eligible_ids(after, LAST_START, LAST_END, LISTED))


# --- comparing the groups --------------------------------------------------

def test_rates_and_the_difference():
    result = compare(n_send=100, bought_send=30, n_hold=50, bought_hold=10)
    assert result.rate_send == pytest.approx(0.30)
    assert result.rate_hold == pytest.approx(0.20)
    assert result.difference == pytest.approx(0.10)


def test_the_interval_surrounds_the_difference():
    result = compare(100, 30, 50, 10)
    assert result.low < result.difference < result.high


def test_a_bigger_sample_gives_a_narrower_interval():
    small = compare(100, 30, 50, 10)
    large = compare(1000, 300, 500, 100)
    assert (large.high - large.low) < (small.high - small.low)


def test_zero_rates_still_give_an_interval_that_includes_zero():
    result = compare(100, 0, 100, 0)
    assert result.low <= 0 <= result.high
    assert result.low < result.high


def test_a_tiny_sample_is_inconclusive_even_with_a_big_gap():
    result = compare(n_send=8, bought_send=8, n_hold=4, bought_hold=0)
    assert result.verdict == 'inconclusive'
    assert 'few' in result.reason.lower()


def test_a_clear_gain_in_a_large_sample_is_positive():
    result = compare(n_send=400, bought_send=200, n_hold=100, bought_hold=25)
    assert result.verdict == 'positive'


def test_a_clear_loss_in_a_large_sample_is_negative():
    result = compare(n_send=400, bought_send=40, n_hold=100, bought_hold=45)
    assert result.verdict == 'negative'


def test_an_interval_that_includes_zero_is_inconclusive():
    result = compare(n_send=MIN_PER_ARM * 2, bought_send=21, n_hold=MIN_PER_ARM, bought_hold=10)
    assert result.low < 0 < result.high
    assert result.verdict == 'inconclusive'


def test_the_smallest_detectable_difference_shrinks_with_more_customers():
    small = compare(100, 30, 25, 6)
    large = compare(1000, 300, 250, 60)
    assert small.mde > large.mde > 0


def test_the_smallest_detectable_difference_is_sensible():
    # 400 vs 100 around a 30% rate: (1.96 + 0.84) * sqrt(.3*.7*(1/400+1/100)) = 0.1435
    assert compare(400, 120, 100, 30).mde == pytest.approx(0.1435, abs=0.003)


def test_an_empty_group_has_no_rates_and_no_verdict():
    result = compare(n_send=50, bought_send=10, n_hold=0, bought_hold=0)
    assert result.difference is None and result.mde is None
    assert result.verdict == 'inconclusive'


def test_more_buyers_than_customers_is_refused():
    with pytest.raises(ValueError):
        compare(10, 11, 10, 0)
