import pandas as pd
import pytest

from src.forecast.series import censored_weeks, weekly_demand

END = pd.Timestamp('2026-09-30')


def sales_frame(rows):
    frame = pd.DataFrame(rows, columns=['date', 'product', 'quantity', 'event'])
    return frame.assign(date=pd.to_datetime(frame['date']))


# Weeks are 7 days ending on END, labelled by their first day:
# 10 Sep to 16 Sep, 17 Sep to 23 Sep, 24 Sep to 30 Sep.
SALES = sales_frame([
    ('2026-09-30', 'Silk Saree', 3, None),   # last day of the newest week
    ('2026-09-24', 'Silk Saree', 2, None),   # first day of the newest week
    ('2026-09-23', 'Silk Saree', 4, None),   # last day of the middle week
    ('2026-09-23', 'Silk Saree', -1, None),  # a refund is not demand
    ('2026-09-10', 'Potli Bag', 5, None),    # first day of the oldest week
])


def test_one_row_per_week_labelled_by_its_first_day():
    demand = weekly_demand(SALES, END)
    assert list(demand.index) == [
        pd.Timestamp('2026-09-10'), pd.Timestamp('2026-09-17'),
        pd.Timestamp('2026-09-24')]


def test_units_are_gross_per_product_with_empty_weeks_as_zero():
    demand = weekly_demand(SALES, END)
    assert list(demand.columns) == ['Potli Bag', 'Silk Saree']
    assert demand['Silk Saree'].tolist() == [0, 4, 5]
    assert demand['Potli Bag'].tolist() == [5, 0, 0]


def test_the_newest_week_ends_on_the_end_date():
    demand = weekly_demand(SALES, END)
    assert demand.index[-1] == END - pd.Timedelta(days=6)


def test_end_defaults_to_the_newest_sale():
    assert weekly_demand(SALES).equals(weekly_demand(SALES, END))


def test_sales_after_the_end_date_are_ignored():
    later = pd.concat([SALES, sales_frame([('2026-10-05', 'Silk Saree', 99, None)])])
    assert weekly_demand(later, END).equals(weekly_demand(SALES, END))


def test_time_of_day_does_not_move_a_sale_to_another_week():
    stamped = SALES.assign(date=SALES['date'] + pd.Timedelta(hours=23))
    assert weekly_demand(stamped, END).equals(weekly_demand(SALES, END))


def test_no_sales_gives_an_empty_frame():
    empty = sales_frame([])
    assert weekly_demand(empty).empty


CENSORED = sales_frame([
    ('2026-09-01', 'Silk Saree', 2, None),
    ('2026-09-12', 'Silk Saree', 0, 'Silk Saree out of stock'),
    ('2026-09-18', 'Silk Saree', 0, 'Silk Saree out of stock'),
    ('2026-09-10', 'Potli Bag', 5, None),
    ('2026-09-30', 'Potli Bag', 1, None),
])


def test_a_stock_out_flags_only_that_products_overlapping_weeks():
    demand = weekly_demand(CENSORED, END)
    mask = censored_weeks(CENSORED, demand.index, demand.columns)
    saree = mask['Silk Saree']
    assert saree[pd.Timestamp('2026-09-10')]
    assert saree[pd.Timestamp('2026-09-17')]
    assert not saree[pd.Timestamp('2026-09-24')]
    assert not mask['Potli Bag'].any()


def test_a_lockdown_flags_every_product():
    sales = sales_frame([
        ('2026-09-10', 'Silk Saree', 2, None),
        ('2026-09-10', 'Potli Bag', 5, None),
        ('2026-09-18', 'Silk Saree', 0, 'COVID lockdown'),
        ('2026-09-30', 'Potli Bag', 1, None),
    ])
    demand = weekly_demand(sales, END)
    mask = censored_weeks(sales, demand.index, demand.columns)
    assert mask.loc[pd.Timestamp('2026-09-17')].all()
    assert not mask.loc[pd.Timestamp('2026-09-10')].any()


def test_no_censoring_events_means_nothing_is_flagged():
    demand = weekly_demand(SALES, END)
    mask = censored_weeks(SALES, demand.index, demand.columns)
    assert mask.shape == demand.shape
    assert not mask.to_numpy().any()
    assert mask.dtypes.eq(bool).all()


@pytest.mark.parametrize('label', ['Diwali sale', 'New stock from Bangladesh'])
def test_other_business_events_are_not_censoring(label):
    sales = sales_frame([
        ('2026-09-10', 'Silk Saree', 2, label),
        ('2026-09-30', 'Silk Saree', 1, None),
    ])
    demand = weekly_demand(sales, END)
    assert not censored_weeks(sales, demand.index, demand.columns).to_numpy().any()
