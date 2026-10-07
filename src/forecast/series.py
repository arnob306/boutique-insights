"""
Weekly demand per product, for checking forecasts against what happened.

Weeks are 7 days ending on the newest day and labelled by their first day,
the same convention as the dashboard's trend charts. Demand is gross units
sold: a refund does not mean the customer wanted the product any less.
"""

from typing import Optional

import pandas as pd

from src.metrics.velocity import censored_windows

WEEK_DAYS = 7
DAY = pd.Timedelta(days=1)


def weekly_demand(sales: pd.DataFrame, end: Optional[pd.Timestamp] = None) -> pd.DataFrame:
    """
    Units sold per week (rows) per product (columns), oldest week first.

    Covers every week from the first sale to ``end`` (default: the newest
    sale), with empty weeks as zero. Sales after ``end`` are ignored.
    """
    days = sales['date'].dt.normalize()
    end = days.max() if end is None else pd.Timestamp(end).normalize()
    bought = sales[(sales['quantity'] > 0) & (days <= end)]
    if bought.empty:
        return pd.DataFrame()

    weeks_back = (end - bought['date'].dt.normalize()).dt.days // WEEK_DAYS
    labels = end - (WEEK_DAYS * weeks_back + WEEK_DAYS - 1) * DAY
    totals = bought['quantity'].groupby([labels, bought['product']]).sum()
    index = pd.date_range(
        labels.min(), end - (WEEK_DAYS - 1) * DAY, freq=f'{WEEK_DAYS}D',
        name='week_start')
    return totals.unstack(fill_value=0).reindex(index, fill_value=0).sort_index(axis=1)


def censored_weeks(
    sales: pd.DataFrame, index: pd.DatetimeIndex, columns: pd.Index
) -> pd.DataFrame:
    """
    True where a product's week overlaps a stock-out or lockdown.

    A week like that says little about demand, so it is left out of scoring.
    """
    mask = pd.DataFrame(False, index=index, columns=columns)
    week_ends = index + (WEEK_DAYS - 1) * DAY
    for window in censored_windows(sales):
        overlapping = (week_ends >= window.start) & (index <= window.end)
        if window.product is None:
            mask.loc[overlapping, :] = True
        elif window.product in mask.columns:
            mask.loc[overlapping, window.product] = True
    return mask
