import pandas as pd
import pytest

from src.metrics.calendar import FestivalWindow
from src.reports.playbook import (
    DEFAULT_LEAD_WEEKS,
    HORIZON_DAYS,
    PLAYBOOK_FOOTER,
    build_playbook,
    lead_weeks_from_stock,
)

TODAY = pd.Timestamp('2026-10-01')
DAY = pd.Timedelta(days=1)


def _window(name='Durga Puja', starts_in=70, length=11):
    start = TODAY + starts_in * DAY
    return FestivalWindow(name, start, start + (length - 1) * DAY)


def _uplift(festival='Durga Puja', rows=(('Saree', 2.6, 5),)):
    return pd.DataFrame(
        [(festival, c, u, w) for c, u, w in rows],
        columns=['festival', 'category', 'uplift', 'windows'])


def _build(windows, uplift=None, lead=6, assumed=False, today=TODAY):
    uplift = _uplift() if uplift is None else uplift
    return build_playbook(windows, uplift, today, lead, assumed)


def _one(**kwargs):
    items = _build(**kwargs)
    assert len(items) == 1
    return items[0]


# --- order-by date and stages ----------------------------------------------

def test_order_by_is_the_festival_start_minus_the_lead_time():
    item = _one(windows=[_window(starts_in=70)], lead=6)
    assert item.order_by == _window(starts_in=70).start - 42 * DAY


def test_a_festival_far_ahead_is_listed_as_later():
    assert _one(windows=[_window(starts_in=100)]).stage == 'later'


def test_order_soon_within_two_weeks_of_the_order_by_date():
    # order-by is 10 days away: start in 52 days, lead 42 days
    assert _one(windows=[_window(starts_in=52)]).stage == 'order_soon'


def test_order_now_once_the_order_by_date_has_passed():
    assert _one(windows=[_window(starts_in=30)]).stage == 'order_now'


def test_order_by_today_counts_as_order_now():
    assert _one(windows=[_window(starts_in=42)]).stage == 'order_now'


def test_preview_when_the_festival_is_two_weeks_away():
    assert _one(windows=[_window(starts_in=14)]).stage == 'preview'
    assert _one(windows=[_window(starts_in=15)]).stage == 'order_now'


def test_on_now_while_the_window_is_open():
    assert _one(windows=[_window(starts_in=-3, length=11)]).stage == 'on_now'
    assert _one(windows=[_window(starts_in=0, length=11)]).stage == 'on_now'


def test_clearance_for_two_weeks_after_the_window_ends():
    ended_10_days_ago = _window(starts_in=-20, length=11)
    assert _one(windows=[ended_10_days_ago]).stage == 'clearance'


def test_a_long_finished_festival_is_dropped():
    assert _build(windows=[_window(starts_in=-40, length=11)]) == ()


def test_a_festival_beyond_the_horizon_is_dropped():
    assert _build(windows=[_window(starts_in=HORIZON_DAYS + 1)]) == ()


def test_festival_on_the_horizon_edge_is_kept():
    assert len(_build(windows=[_window(starts_in=HORIZON_DAYS)])) == 1


def test_items_are_in_date_order():
    late = _window('Christmas', starts_in=80)
    early = _window('Durga Puja', starts_in=60)
    names = [i.festival for i in _build(windows=[late, early])]
    assert names == ['Durga Puja', 'Christmas']


def test_no_calendar_means_no_playbook():
    assert _build(windows=[]) == ()


# --- expected lift and confidence ------------------------------------------

def test_lift_shows_the_category_the_factor_and_the_sample_size():
    item = _one(windows=[_window()])
    lift = item.lifts[0]
    assert (lift.category, lift.uplift, lift.windows) == ('Saree', 2.6, 5)
    assert 'Saree' in item.text and '2.6x' in item.text
    assert '5 past years' in item.text


@pytest.mark.parametrize('windows, label', [(2, 'rough'), (3, 'rough'),
                                            (4, 'solid'), (9, 'solid')])
def test_confidence_comes_from_how_many_past_windows_back_it(windows, label):
    uplift = _uplift(rows=[('Saree', 2.6, windows)])
    assert _one(windows=[_window()], uplift=uplift).lifts[0].confidence == label


def test_a_small_lift_is_not_shown():
    uplift = _uplift(rows=[('Saree', 1.2, 5)])
    item = _one(windows=[_window()], uplift=uplift)
    assert item.lifts == ()
    assert 'faster' not in item.text


def test_only_the_two_biggest_lifts_are_shown():
    uplift = _uplift(rows=[('Saree', 2.0, 5), ('Jewellery', 3.3, 5),
                           ('Bangles', 1.8, 5)])
    lifts = _one(windows=[_window()], uplift=uplift).lifts
    assert [l.category for l in lifts] == ['Jewellery', 'Saree']


def test_a_festival_with_no_history_still_gets_dates_but_no_lift():
    item = _one(windows=[_window('Saraswati Puja')])
    assert item.lifts == ()
    assert 'Saraswati Puja' in item.text
    assert 'no past sales' in item.text.lower()


def test_lift_only_comes_from_the_matching_festival():
    uplift = _uplift('Christmas', rows=[('Jewellery', 2.6, 5)])
    assert _one(windows=[_window('Durga Puja')], uplift=uplift).lifts == ()


# --- wording ---------------------------------------------------------------

def test_order_now_says_to_order_and_gives_the_order_by_date():
    item = _one(windows=[_window(starts_in=30)])
    assert 'order now' in item.text.lower()
    assert f'{item.order_by:%d %b}' in item.text


def test_an_assumed_lead_time_is_labelled_as_an_assumption():
    item = _one(windows=[_window()], assumed=True)
    assert 'assum' in item.text.lower()
    assert '6 weeks' in item.text


def test_a_real_lead_time_is_not_called_an_assumption():
    assert 'assum' not in _one(windows=[_window()], assumed=False).text.lower()


def test_preview_says_it_is_too_late_to_order_a_shipment():
    assert 'too late' in _one(windows=[_window(starts_in=10)]).text.lower()


def test_clearance_says_to_sell_down_leftover_stock():
    text = _one(windows=[_window(starts_in=-20, length=11)]).text.lower()
    assert 'leftover' in text


def test_the_footer_says_this_is_not_financial_advice():
    assert 'not financial advice' in PLAYBOOK_FOOTER
    assert 'estimate' in PLAYBOOK_FOOTER.lower()


# --- lead time from the stock sheet ----------------------------------------

def test_no_stock_sheet_uses_the_default_lead_time_as_an_assumption():
    assert lead_weeks_from_stock(None) == (DEFAULT_LEAD_WEEKS, True)


def test_the_stock_sheets_median_lead_time_is_used_when_present():
    stock = pd.DataFrame({'weeks_to_arrive': [2, 4, 10]})
    assert lead_weeks_from_stock(stock) == (4, False)


def test_an_empty_stock_sheet_falls_back_to_the_default():
    stock = pd.DataFrame({'weeks_to_arrive': []})
    assert lead_weeks_from_stock(stock) == (DEFAULT_LEAD_WEEKS, True)
