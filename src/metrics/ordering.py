"""
How many of each product to order for a festival.

This is the newsvendor idea: order too few and each missed sale loses its
margin, order too many and each leftover loses part of its cost (what she
cannot get back by selling it later). The best quantity is the demand level
that is only exceeded with probability ``1 - ratio``, where

    ratio = margin / (margin + leftover loss).

Demand is not known, so the spread comes from backtesting the same forecast on
past festivals. This is a planning guide, not financial advice.
"""

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np

MAX_RATIO = 0.95  # never plan for certainty: the last few units are rarely sold
NEIGHBOURS = 60  # past cases of similar size that a spread is read from


def critical_ratio(unit_price: float, unit_cost: float, resale_share: float) -> float:
    """The chance of covering demand that balances a missed sale and a leftover.

    ``resale_share`` is the share of the normal price a leftover later sells
    for. Returns 0 when a sale earns nothing, and ``MAX_RATIO`` when a
    leftover costs nothing.
    """
    if not 0.0 <= resale_share <= 1.0:
        raise ValueError(f'resale share must be between 0 and 1, got {resale_share}')
    underage = unit_price - unit_cost
    if underage <= 0:
        return 0.0
    overage = unit_cost - resale_share * unit_price
    if overage <= 0:
        return MAX_RATIO
    return min(MAX_RATIO, underage / (underage + overage))


def order_quantity(expected_units: float, ratios: np.ndarray, critical: float) -> Optional[int]:
    """Whole items to cover ``critical`` of the cases seen in past festivals.

    ``ratios`` are actual over forecast units from the backtest. Returns None
    when there is no spread to work from.
    """
    if len(ratios) == 0:
        return None
    if critical <= 0:
        return 0
    level = float(np.quantile(expected_units * ratios, critical))
    return max(0, math.ceil(level - 1e-9))


def similar_cases(expected_units: float, forecasts: np.ndarray, ratios: np.ndarray,
                  k: int = NEIGHBOURS) -> np.ndarray:
    """The ratios of the ``k`` past cases whose forecast was closest in size.

    Small forecasts miss by far more, in proportion, than large ones (one
    extra sale is a big ratio on 1 item and a small one on 30), so a spread
    pooled over all sizes would overstock the big sellers.
    """
    if len(ratios) == 0 or expected_units <= 0:
        return ratios[:0]
    distance = np.abs(np.log(forecasts) - math.log(expected_units))
    return ratios[np.argsort(distance, kind='stable')[:k]]


def sellout_chance(quantity: int, scenarios: np.ndarray) -> float:
    """Share of cases where demand uses up ``quantity``."""
    return float(np.mean(scenarios >= quantity))


def expected_leftover(quantity: int, scenarios: np.ndarray) -> float:
    """Average items left unsold."""
    return float(np.mean(np.maximum(quantity - scenarios, 0.0)))


def realised_profit(quantity: int, demand: float, underage: float, overage: float) -> float:
    """What an order earned once demand was known: margin on what sold, less
    the cost lost on what was left."""
    return underage * min(quantity, demand) - overage * max(quantity - demand, 0.0)


@dataclass(frozen=True, eq=False)
class Candidate:
    """One product's planned quantity, with what the budget trim needs."""
    product: str
    quantity: int
    unit_cost: float
    underage: float  # margin lost on a missed sale
    overage: float  # cost lost on a leftover
    scenarios: np.ndarray  # possible demand, in units


def _last_unit_value(item: Candidate, quantity: int) -> float:
    """Expected profit of the ``quantity``-th unit, per dollar spent on it."""
    sells = float(np.mean(item.scenarios >= quantity))
    profit = sells * item.underage - (1.0 - sells) * item.overage
    return profit / item.unit_cost if item.unit_cost > 0 else math.inf


def trim_to_budget(items: List[Candidate], budget: Optional[float]) -> Dict[str, int]:
    """Quantities cut back until their cost fits ``budget``.

    The unit that earns least per dollar is dropped first, so the money goes
    to the products that return most. The inputs are not changed.
    """
    kept = {item.product: item.quantity for item in items}
    if budget is None:
        return kept
    by_product = {item.product: item for item in items}
    spend = sum(item.unit_cost * item.quantity for item in items)
    while spend > budget + 1e-9:
        live = [name for name, qty in kept.items() if qty > 0]
        if not live:
            break
        worst = min(live, key=lambda name: (
            _last_unit_value(by_product[name], kept[name]), name))
        kept[worst] -= 1
        spend -= by_product[worst].unit_cost
    return kept
