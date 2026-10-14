"""
What the stock sheet says about itself: how old the counts are, which products
to count again, and which have far more stock than will sell soon.

Counts and product names only, never customer details.
"""

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import pandas as pd

from src.validation.checks import STOCK_STALE_AFTER_DAYS

OVERSTOCK_WEEKS = 26  # about half a year of cover at the current pace
OVERSTOCK_MIN_UNITS = 5  # a few spare items are not worth a sale
MAX_OVERSTOCK_LINES = 2
MAX_RECOUNT = 5


@dataclass(frozen=True)
class OverstockLine:
    product: str
    stock_now: int
    weeks_of_cover: float


def stock_age_days(stock: Optional[pd.DataFrame], today: pd.Timestamp) -> Optional[int]:
    """Days since the newest count on the sheet, or None without a sheet."""
    if stock is None or stock.empty:
        return None
    return int((today - stock['count_date'].max()).days)


def recount_products(
    stock: Optional[pd.DataFrame], products: Sequence[str], today: pd.Timestamp,
) -> Tuple[str, ...]:
    """Of these products, the ones counted too long ago, oldest count first."""
    if stock is None or stock.empty:
        return ()
    counted = stock.groupby('product')['count_date'].max()
    ages = {p: (today - counted[p]).days for p in products if p in counted.index}
    stale = [p for p, age in ages.items() if age > STOCK_STALE_AFTER_DAYS]
    stale.sort(key=lambda p: -ages[p])
    return tuple(stale[:MAX_RECOUNT])


def overstock_lines(suggestions: pd.DataFrame) -> Tuple[OverstockLine, ...]:
    """Products with very long cover that are still selling, longest cover first."""
    if suggestions.empty:
        return ()
    cover = suggestions['weeks_of_cover']
    slow = suggestions[
        (suggestions['units_per_week'] > 0)
        & (cover > OVERSTOCK_WEEKS)
        & (suggestions['stock_now'] >= OVERSTOCK_MIN_UNITS)
    ].sort_values('weeks_of_cover', ascending=False).head(MAX_OVERSTOCK_LINES)
    return tuple(
        OverstockLine(str(product), int(units), float(weeks))
        for product, units, weeks in zip(
            slow['product'].tolist(), slow['stock_now'].tolist(),
            slow['weeks_of_cover'].tolist(), strict=True)
    )
