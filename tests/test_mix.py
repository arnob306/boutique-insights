import pandas as pd
import pytest
from test_metrics import make_sales

from src.metrics.mix import category_mix, channel_mix, mix_notes

AS_OF = pd.Timestamp('2026-01-01')
DAY = pd.Timedelta(days=1)
NOW = AS_OF - 30 * DAY  # inside the last 12 months
BEFORE = AS_OF - 400 * DAY  # inside the 12 months before that


def _sales(rows, channels=None):
    sales = make_sales(rows)
    if channels is not None:
        sales['channel'] = channels
    return sales


def _row(category='Saree', price=100.0, cost=60.0, date=NOW, qty=1):
    return {'date': date, 'category': category, 'price': price, 'cost': cost, 'qty': qty}


def _by_name(lines, field='category'):
    return {getattr(l, field): l for l in lines}


# --- categories -------------------------------------------------------------

def test_categories_are_ranked_by_their_share_of_sales():
    lines = category_mix(_sales([_row('Saree', 300), _row('Bangles', 100)]), AS_OF)
    assert [l.category for l in lines] == ['Saree', 'Bangles']
    assert [l.revenue_share for l in lines] == pytest.approx([0.75, 0.25])


def test_margin_is_profit_over_sales_on_costed_rows():
    line = category_mix(_sales([_row('Saree', 100, cost=60)]), AS_OF)[0]
    assert line.margin == pytest.approx(0.40)


def test_a_category_with_no_costs_has_no_margin():
    line = category_mix(_sales([_row('Saree', 100, cost=None)]), AS_OF)[0]
    assert line.margin is None


def test_refunds_reduce_a_categorys_sales():
    rows = [_row('Saree', 100), _row('Saree', 100, qty=-1), _row('Bangles', 100)]
    lines = _by_name(category_mix(_sales(rows), AS_OF))
    assert lines['Saree'].revenue_share == pytest.approx(0.0)
    assert lines['Bangles'].revenue_share == pytest.approx(1.0)


def test_only_the_last_twelve_months_count():
    rows = [_row('Saree', 100), _row('Bangles', 900, date=AS_OF - 800 * DAY)]
    lines = category_mix(_sales(rows), AS_OF)
    assert [l.category for l in lines] == ['Saree']


def test_the_share_change_compares_with_the_twelve_months_before():
    rows = [_row('Saree', 300), _row('Bangles', 100),
            _row('Saree', 100, date=BEFORE), _row('Bangles', 100, date=BEFORE)]
    lines = _by_name(category_mix(_sales(rows), AS_OF))
    assert lines['Saree'].share_change == pytest.approx(0.25)
    assert lines['Bangles'].share_change == pytest.approx(-0.25)


def test_without_an_earlier_year_there_is_no_change():
    lines = category_mix(_sales([_row('Saree', 300)]), AS_OF)
    assert lines[0].share_change is None


def test_no_sales_gives_no_categories():
    assert category_mix(_sales([_row(date=AS_OF - 900 * DAY)]), AS_OF) == ()


# --- channels ---------------------------------------------------------------

def test_channels_are_ranked_by_share_of_sales_and_unknown_is_left_out():
    rows = [_row(price=300), _row(price=100), _row(price=500)]
    sales = _sales(rows, ['Facebook / Instagram', 'Word of mouth', None])
    lines = channel_mix(sales, AS_OF)
    assert [l.channel for l in lines] == ['Facebook / Instagram', 'Word of mouth']
    assert [l.share for l in lines] == pytest.approx([0.75, 0.25])


def test_the_channel_change_compares_with_the_year_before():
    rows = [_row(price=300), _row(price=100),
            _row(price=100, date=BEFORE), _row(price=100, date=BEFORE)]
    sales = _sales(rows, ['Facebook / Instagram', 'Word of mouth',
                          'Facebook / Instagram', 'Word of mouth'])
    lines = _by_name(channel_mix(sales, AS_OF), 'channel')
    assert lines['Facebook / Instagram'].share_change == pytest.approx(0.25)


def test_no_channel_data_gives_no_channels():
    assert channel_mix(_sales([_row()]), AS_OF) == ()


# --- the plain-English notes -------------------------------------------------

def _notes(rows, channels=None):
    sales = _sales(rows, channels)
    return mix_notes(category_mix(sales, AS_OF), channel_mix(sales, AS_OF))


def test_the_notes_name_the_biggest_category_and_its_margin():
    notes = _notes([_row('Saree', 600, cost=360), _row('Bangles', 100, cost=40)])
    assert any('Saree' in n and '86%' in n and '40% margin' in n for n in notes)


def test_the_notes_point_out_a_small_category_with_a_better_margin():
    notes = _notes([_row('Saree', 600, cost=360), _row('Bangles', 100, cost=40)])
    assert any('Bangles' in n and 'best margin (60%)' in n and '14% of sales' in n
               for n in notes)


def test_no_better_margin_note_when_the_top_category_has_the_best_margin():
    notes = _notes([_row('Saree', 600, cost=200), _row('Bangles', 100, cost=90)])
    assert not any('best margin' in n for n in notes)


def test_the_notes_name_the_biggest_channel_and_how_it_moved():
    rows = [_row(price=300), _row(price=100),
            _row(price=100, date=BEFORE), _row(price=100, date=BEFORE)]
    channels = ['Facebook / Instagram', 'Word of mouth'] * 2
    notes = _notes(rows, channels)
    assert any('Facebook / Instagram' in n and '75% of sales' in n
               and '25 points up' in n for n in notes)


def test_a_small_channel_move_is_not_mentioned():
    rows = [_row(price=100), _row(price=100),
            _row(price=100, date=BEFORE), _row(price=100, date=BEFORE)]
    channels = ['Facebook / Instagram', 'Word of mouth'] * 2
    assert not any('points' in n for n in _notes(rows, channels))


def test_no_data_gives_no_notes():
    assert mix_notes((), ()) == ()
