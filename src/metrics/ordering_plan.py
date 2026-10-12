"""
What to order for one festival: a quantity for each product.

Each product's expected units (``ordering_forecast``) and the spread seen on
past festivals give a range of possible demand. The quantity is the level that
balances a missed sale against a leftover (``ordering``), less the stock she
already has or has on order, and raised to the supplier's minimum. If the
cost is more than the budget, the units that earn least per dollar are dropped
first. This is a planning guide, not financial advice.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.metrics.ordering import (
    Candidate,
    critical_ratio,
    expected_leftover,
    order_quantity,
    sellout_chance,
    similar_cases,
    trim_to_budget,
)
from src.metrics.ordering_forecast import spread_cases, unit_forecasts
from src.metrics.reorder import confidence_label, current_stock
from src.metrics.velocity import weekly_velocity

PRICE_DAYS = 365  # unit prices are taken from the last year of sales


@dataclass(frozen=True)
class OrderLine:
    product: str
    to_order: int  # items to buy now, after stock and the minimum order
    expected_units: float  # forecast festival sales
    have: int  # on the shelf and on order
    unit_cost: float
    sellout_chance: float  # chance demand uses up shelf plus order
    expected_leftover: float  # items left unsold, on average
    confidence: str  # how much recent selling the forecast rests on


@dataclass(frozen=True)
class OrderPlan:
    festival: str
    lines: Tuple[OrderLine, ...]  # biggest spend first
    total_cost: float
    budget: Optional[float]
    trimmed: bool  # the budget cut the plan back
    resale_share: float
    spread_samples: int  # past product-windows the spread comes from
    stock_known: bool  # False: stock on hand was not taken off


def unit_economics(sales: pd.DataFrame, today: pd.Timestamp) -> Dict[str, Tuple[float, float]]:
    """Each product's usual price and latest cost, from before ``today``."""
    past = sales[(sales['date'] < today) & (sales['quantity'] > 0)]
    recent = past[past['date'] >= today - pd.Timedelta(days=PRICE_DAYS)]
    latest = past.sort_values('date').dropna(subset=['unit_cost']).groupby(
        'product')['unit_cost'].last()
    result = {}
    for product, cost in latest.items():
        rows = recent[recent['product'] == product]
        rows = rows if len(rows) else past[past['product'] == product]
        result[str(product)] = (float(rows['unit_price'].median()), float(cost))
    return result


def _stock_by_product(sales, stock: Optional[pd.DataFrame], today) -> Dict[str, Tuple[int, int]]:
    """Product -> (items held or on order, minimum order)."""
    if stock is None or stock.empty:
        return {}
    held = {}
    for row in stock.to_dict('records'):
        shelf = current_stock(sales, row['product'], row['stock_on_hand'],
                              row['count_date'], today)
        coming = int(row.get('on_order', 0))
        held[str(row['product'])] = (shelf + coming, max(1, int(row['min_order_qty'])))
    return held


def build_order_plan(sales: pd.DataFrame, stock: Optional[pd.DataFrame], window, today,
                     lead_weeks: int, resale_share: float,
                     budget: Optional[float]) -> Optional[OrderPlan]:
    """Quantities for ``window``, or None when there is too little history."""
    if not 0.0 <= resale_share <= 1.0:
        raise ValueError(f'resale share must be between 0 and 1, got {resale_share}')
    today = pd.Timestamp(today).normalize()
    expected = unit_forecasts(sales, window, today, lead_weeks)
    forecasts, pooled = spread_cases(sales, today, lead_weeks)
    if not expected or len(pooled) == 0:
        return None
    economics = unit_economics(sales, today)
    held = _stock_by_product(sales, stock, today)
    sales_counts = weekly_velocity(sales, today)['sales_count'].to_dict()

    candidates: List[Candidate] = []
    details: Dict[str, Tuple[float, int, int, float, np.ndarray]] = {}
    for product, units in expected.items():
        if product not in economics:
            continue
        price, cost = economics[product]
        ratio = critical_ratio(price, cost, resale_share)
        spread = similar_cases(units, forecasts, pooled)
        target = order_quantity(units, spread, ratio)
        if not target:
            continue
        have, least = held.get(product, (0, 1))
        need = max(0, target - have)
        quantity = max(need, least) if need else 0
        scenarios = units * spread
        candidates.append(Candidate(
            product=product, quantity=quantity, unit_cost=cost, underage=price - cost,
            overage=cost - resale_share * price,
            scenarios=np.maximum(scenarios - have, 0.0)))
        details[product] = (units, have, least, cost, scenarios)

    kept = trim_to_budget(candidates, budget)
    lines = []
    for item in candidates:
        units, have, least, cost, scenarios = details[item.product]
        final = kept[item.product]
        final = 0 if 0 < final < least else final
        lines.append(OrderLine(
            product=item.product, to_order=final, expected_units=units, have=have,
            unit_cost=cost,
            sellout_chance=sellout_chance(have + final, scenarios),
            expected_leftover=expected_leftover(have + final, scenarios),
            confidence=confidence_label(int(sales_counts.get(item.product, 0)))))
    lines.sort(key=lambda line: (-line.to_order * line.unit_cost, line.product))
    wanted = {item.product: item.quantity for item in candidates}
    trimmed = any(line.to_order != wanted[line.product] for line in lines)
    return OrderPlan(
        festival=window.name, lines=tuple(lines),
        total_cost=sum(line.to_order * line.unit_cost for line in lines),
        budget=budget, trimmed=trimmed, resale_share=resale_share,
        spread_samples=len(pooled), stock_known=bool(held))
