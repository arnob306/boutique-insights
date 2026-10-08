"""Holding some festival regulars back on purpose, for a fair test."""

import pandas as pd

from src.holdout import HOLD, SEND, Holdout, eligible_ids
from src.metrics.calendar import FestivalWindow
from src.privacy import hash_customer
from src.reminders import (
    MAX_LINES,
    RECENT_BUYER_DAYS,
    build_festival_reminders,
    build_reminders,
    render_reminders,
)

TODAY = pd.Timestamp('2026-10-01')
DAY = pd.Timedelta(days=1)
SALT = 'unit-test-salt-0123456789'
SUBURB = 'Springvale'
WINDOW = FestivalWindow('Durga Puja', TODAY + 20 * DAY, TODAY + 31 * DAY)
LAST_START = WINDOW.start - 364 * DAY
LAST_END = WINDOW.end - 364 * DAY
PAST = pd.DataFrame([{'festival': 'Durga Puja', 'start': LAST_START, 'end': LAST_END,
                      'rows': 10}])


def names(count):
    return [f'Cust {i:03d}' for i in range(count)]


def rows(customers, recent=()):
    """Everyone bought in last year's window; ``recent`` also shopped lately."""
    records = [{'date': LAST_START + 3 * DAY, 'name': n, 'suburb': SUBURB,
                'category': 'Saree', 'quantity': 1, 'amount': 100.0 + i}
               for i, n in enumerate(customers)]
    records += [{'date': TODAY - RECENT_BUYER_DAYS * DAY, 'name': n, 'suburb': SUBURB,
                 'category': 'Saree', 'quantity': 1, 'amount': 10.0} for n in recent]
    return pd.DataFrame(records, columns=['date', 'name', 'suburb', 'category',
                                          'quantity', 'amount'])


def build(customers, share=0.3, recent=()):
    holdout = Holdout(share, SALT) if share else None
    found = build_festival_reminders(rows(customers, recent), [WINDOW], PAST, TODAY,
                                     holdout=holdout)
    return found[0]


def arm_of(name, share=0.3):
    return Holdout(share, SALT).arm(hash_customer(name, SUBURB, SALT), 'Durga Puja',
                                    WINDOW.start.year)


def test_without_a_holdout_nobody_is_held_back():
    result = build(names(40), share=None)
    assert result.held_back == 0 and result.count == 40


def test_held_back_customers_are_not_on_the_list():
    result = build(names(60))
    listed = [r.name for r in result.regulars]
    assert result.held_back > 0
    assert all(arm_of(n) == SEND for n in listed)


def test_the_count_is_who_to_contact_and_the_rest_are_held_back():
    result = build(names(60))
    held = sum(arm_of(n) == HOLD for n in names(60))
    assert result.held_back == held
    assert result.count == 60 - held


def test_the_list_cap_applies_after_the_holdout():
    result = build(names(80))
    assert len(result.regulars) == MAX_LINES
    assert result.count > MAX_LINES


def test_recent_buyers_are_neither_listed_nor_counted_as_held_back():
    customers = names(60)
    result = build(customers, recent=customers[:30])
    assert result.count + result.held_back == 30


def test_the_split_matches_what_the_scoring_will_recompute_from_hashed_sales():
    customers = names(60)
    hashed = pd.DataFrame({
        'date': rows(customers)['date'],
        'customer_id': [hash_customer(n, SUBURB, SALT) for n in customers],
        'quantity': 1})
    eligible = eligible_ids(hashed, LAST_START, LAST_END, TODAY)
    result = build(customers)
    sending = sum(Holdout(0.3, SALT).arm(i, 'Durga Puja', WINDOW.start.year) == SEND
                  for i in eligible)
    assert result.count + result.held_back == len(eligible)
    assert result.count == sending


def test_the_file_says_how_many_were_held_back_but_not_who():
    customers = names(60)
    result = build(customers)
    text = render_reminders(build_reminders(rows(customers), TODAY), TODAY, [result])
    assert f'{result.held_back} held back on purpose' in text
    held_names = [n for n in customers if arm_of(n) == HOLD]
    assert held_names and not any(n in text for n in held_names)


def test_the_file_is_silent_about_holdouts_when_there_is_none():
    customers = names(20)
    result = build(customers, share=None)
    text = render_reminders(build_reminders(rows(customers), TODAY), TODAY, [result])
    assert 'held back' not in text


def test_a_list_where_everyone_is_held_back_is_still_returned_for_the_record():
    held = next(n for n in names(200) if arm_of(n, 0.5) == HOLD)
    result = build([held], share=0.5)
    assert (result.count, result.held_back, result.regulars) == (0, 1, ())
    text = render_reminders(build_reminders(rows([held]), TODAY), TODAY, [result])
    assert 'held back on purpose' in text and held not in text
