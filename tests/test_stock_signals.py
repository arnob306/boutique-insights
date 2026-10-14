import math

import pandas as pd

from src.reports.stock_signals import (
    MAX_OVERSTOCK_LINES,
    MAX_RECOUNT,
    OVERSTOCK_MIN_UNITS,
    OVERSTOCK_WEEKS,
    OverstockLine,
    overstock_lines,
    recount_products,
    stock_age_days,
)
from src.validation.checks import STOCK_STALE_AFTER_DAYS

TODAY = pd.Timestamp('2026-10-14')


def _stock(ages):
    """A stock sheet where each product was counted this many days ago."""
    return pd.DataFrame({
        'product': list(ages),
        'count_date': [TODAY - pd.Timedelta(days=d) for d in ages.values()],
    })


def _suggestions(rows):
    """Reorder suggestions from (product, stock_now, units_per_week) rows."""
    return pd.DataFrame([
        {'product': p, 'stock_now': s, 'units_per_week': w,
         'weeks_of_cover': s / w if w > 0 else math.inf}
        for p, s, w in rows
    ], columns=['product', 'stock_now', 'units_per_week', 'weeks_of_cover'])


# --- how old the stock numbers are ------------------------------------------------

def test_stock_age_is_days_since_the_newest_count():
    assert stock_age_days(_stock({'A': 40, 'B': 5}), TODAY) == 5


def test_stock_age_is_none_without_a_stock_sheet():
    assert stock_age_days(None, TODAY) is None


def test_stock_age_is_none_for_an_empty_stock_sheet():
    assert stock_age_days(_stock({}), TODAY) is None


# --- which products to recount ----------------------------------------------------

def test_recount_lists_only_stale_products_among_those_asked_about_oldest_first():
    stock = _stock({'Fresh': 2, 'Old': 40, 'Older': 90, 'Unasked': 200})
    assert recount_products(stock, ['Fresh', 'Old', 'Older'], TODAY) == ('Older', 'Old')


def test_a_count_exactly_at_the_limit_is_not_stale_one_day_more_is():
    stock = _stock({'Edge': STOCK_STALE_AFTER_DAYS, 'Over': STOCK_STALE_AFTER_DAYS + 1})
    assert recount_products(stock, ['Edge', 'Over'], TODAY) == ('Over',)


def test_recount_is_capped():
    ages = {f'P{i}': 60 + i for i in range(MAX_RECOUNT + 3)}
    asked = list(ages)
    assert len(recount_products(_stock(ages), asked, TODAY)) == MAX_RECOUNT


def test_recount_skips_products_missing_from_the_stock_sheet():
    assert recount_products(_stock({'A': 60}), ['A', 'Ghost'], TODAY) == ('A',)


def test_recount_is_empty_without_a_stock_sheet():
    assert recount_products(None, ['A'], TODAY) == ()


def test_recount_uses_the_newest_count_when_a_product_appears_twice():
    stock = pd.DataFrame({
        'product': ['A', 'A'],
        'count_date': [TODAY - pd.Timedelta(days=90), TODAY - pd.Timedelta(days=2)],
    })
    assert recount_products(stock, ['A'], TODAY) == ()


# --- which products have too much stock -------------------------------------------

def test_long_cover_is_flagged_with_the_figures():
    found = overstock_lines(_suggestions([('Old Kurta', 40, 1.0)]))
    assert found == (OverstockLine('Old Kurta', 40, 40.0),)


def test_cover_exactly_at_the_limit_is_not_flagged():
    units = OVERSTOCK_WEEKS * 2
    assert overstock_lines(_suggestions([('Edge', units, 2.0)])) == ()


def test_a_product_with_no_recent_sales_is_not_called_overstock():
    assert overstock_lines(_suggestions([('Dead', 50, 0.0)])) == ()


def test_a_handful_of_units_is_not_overstock_however_slow_it_sells():
    few = OVERSTOCK_MIN_UNITS - 1
    assert overstock_lines(_suggestions([('Rare', few, 0.01)])) == ()


def test_most_overstocked_come_first_and_the_list_is_capped():
    rows = [(f'P{i}', 100 + i * 10, 1.0) for i in range(MAX_OVERSTOCK_LINES + 2)]
    found = overstock_lines(_suggestions(rows))
    assert len(found) == MAX_OVERSTOCK_LINES
    covers = [line.weeks_of_cover for line in found]
    assert covers == sorted(covers, reverse=True)


def test_no_suggestions_means_no_overstock():
    assert overstock_lines(_suggestions([])) == ()
