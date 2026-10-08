from datetime import date

import pandas as pd
import pytest

from src.decisions.experiments import (
    evaluate_experiments,
    pooled_comparison,
    record_experiments,
)
from src.decisions.store import open_log
from src.holdout import HOLD, SEND, arm_for
from src.reminders import FestivalList

SALT = 'unit-test-salt-0123456789'
SHARE = 0.3
LISTED = date(2026, 9, 20)
START, END = pd.Timestamp('2026-10-10'), pd.Timestamp('2026-10-21')
LAST_START, LAST_END = pd.Timestamp('2025-10-11'), pd.Timestamp('2025-10-22')
IDS = [f'{i:016x}' for i in range(200)]


def make_list(count=14, held=6, festival='Durga Puja', start=START, end=END):
    return FestivalList(festival, start, end, LAST_START, LAST_END, (), count, held)


def arm(customer_id):
    return arm_for(customer_id, 'Durga Puja', 2026, SALT, SHARE)


def sales_frame(*rows):
    frame = pd.DataFrame(rows, columns=['date', 'customer_id', 'quantity'])
    return frame.assign(date=pd.to_datetime(frame['date']))


def last_year_sales():
    return [('2025-10-15', i, 1) for i in IDS]


@pytest.fixture
def log_path(tmp_path):
    return tmp_path / 'decisions.sqlite'


def record(log_path, lists, on=LISTED):
    with open_log(log_path) as conn:
        return record_experiments(conn, lists, SHARE, on)


def evaluate(log_path, sales, on=date(2026, 11, 1)):
    with open_log(log_path) as conn:
        return evaluate_experiments(conn, sales, SALT, on)


def outcome(log_path):
    with open_log(log_path) as conn:
        return conn.execute(
            'SELECT n_send, n_hold, bought_send, bought_hold FROM experiment_outcomes'
        ).fetchall()


# --- recording -------------------------------------------------------------

def test_a_list_is_recorded_with_counts_and_the_share(log_path):
    assert record(log_path, [make_list()]) == 1
    with open_log(log_path) as conn:
        row = conn.execute(
            'SELECT festival, window_start, window_end, listed_on, holdout_share, '
            'n_send, n_hold FROM experiments').fetchone()
    assert row == ('Durga Puja', '2026-10-10', '2026-10-21', '2026-09-20', SHARE, 14, 6)


def test_a_list_with_nobody_held_back_is_not_an_experiment(log_path):
    assert record(log_path, [make_list(held=0)]) == 0


def test_the_first_assignment_for_a_festival_is_kept(log_path):
    record(log_path, [make_list()])
    assert record(log_path, [make_list(count=9, held=4)], date(2026, 9, 27)) == 0
    with open_log(log_path) as conn:
        assert conn.execute('SELECT listed_on, n_send FROM experiments').fetchall() == [
            ('2026-09-20', 14)]


def test_no_names_or_ids_are_stored(log_path):
    record(log_path, [make_list()])
    with open_log(log_path) as conn:
        columns = {row[1] for table in ('experiments', 'experiment_outcomes')
                   for row in conn.execute(f'PRAGMA table_info({table})')}
    assert not {c for c in columns if 'name' in c or 'customer' in c}


# --- scoring ---------------------------------------------------------------

def test_not_scored_until_the_festival_is_over(log_path):
    record(log_path, [make_list()])
    data = sales_frame(*last_year_sales(), ('2026-10-20', IDS[0], 1))
    assert evaluate(log_path, data) == 0


def test_buyers_are_counted_per_group_from_the_day_after_the_list_to_the_festival_end(
        log_path):
    record(log_path, [make_list()])
    sending = [i for i in IDS if arm(i) == SEND]
    holding = [i for i in IDS if arm(i) == HOLD]
    data = sales_frame(
        *last_year_sales(),
        ('2026-09-20', sending[0], 1),   # on the list day: a recent buyer, not eligible
        ('2026-09-21', sending[1], 1),   # first counting day
        ('2026-10-21', sending[2], 1),   # last counting day
        ('2026-10-22', sending[3], 1),   # after the festival
        ('2026-10-05', sending[4], -1),  # a refund is not a purchase
        ('2026-10-12', holding[0], 2),
        ('2026-10-13', holding[0], 1),   # twice is still one buyer
        ('2026-10-22', IDS[0], 1))       # also shows the data reaches past the window
    assert evaluate(log_path, data) == 1
    assert outcome(log_path) == [(len(sending) - 1, len(holding), 2, 1)]


def test_people_who_shopped_just_before_the_list_were_never_eligible(log_path):
    record(log_path, [make_list()])
    recent = IDS[0]
    data = sales_frame(*last_year_sales(), ('2026-09-01', recent, 1),
                       ('2026-10-22', IDS[1], 1))
    evaluate(log_path, data)
    n_send, n_hold, *_ = outcome(log_path)[0]
    assert n_send + n_hold == len(IDS) - 1


def test_scoring_twice_writes_once(log_path):
    record(log_path, [make_list()])
    data = sales_frame(*last_year_sales(), ('2026-10-22', IDS[0], 1))
    assert evaluate(log_path, data) == 1
    assert evaluate(log_path, data) == 0


def test_empty_sales_scores_nothing(log_path):
    record(log_path, [make_list()])
    assert evaluate(log_path, sales_frame()) == 0


# --- pooled result ---------------------------------------------------------

def pooled(log_path):
    with open_log(log_path) as conn:
        return pooled_comparison(conn)


def test_nothing_is_pooled_before_anything_is_scored(log_path):
    record(log_path, [make_list()])
    assert pooled(log_path) is None


def test_scored_festivals_are_added_together(log_path):
    with open_log(log_path) as conn:
        for i, festival in enumerate(['Durga Puja', 'Diwali'], start=1):
            conn.execute(
                'INSERT INTO experiments (festival, window_start, window_end, listed_on, '
                'last_start, last_end, holdout_share, n_send, n_hold) VALUES '
                "(?, '2026-10-10', '2026-10-21', '2026-09-20', '2025-10-11', '2025-10-22', "
                '0.3, 10, 4)', (festival,))
            conn.execute(
                'INSERT INTO experiment_outcomes VALUES (?, ?, ?, ?, ?, ?)',
                (i, '2026-11-01', 10, 4, 3 * i, i))
    result = pooled(log_path)
    assert (result.n_send, result.bought_send) == (20, 9)
    assert (result.n_hold, result.bought_hold) == (8, 3)
    assert result.verdict == 'inconclusive'
