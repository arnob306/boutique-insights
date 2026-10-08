"""
The festival playbook: what to do about each upcoming festival, and when.

For every festival in the calendar this works out the date to order by (the
festival start minus the shipment lead time), a stage that says what to do
today, and which categories usually sell faster, with how many past festivals
that figure rests on. It is plain arithmetic on the calendar and the learned
uplift, so it is a planning guide, not financial advice.
"""

from dataclasses import dataclass
from typing import Callable, Optional, Tuple

import pandas as pd

from src.metrics.cashplan import CashPlan
from src.reports.formatting import money

HORIZON_DAYS = 120
DEFAULT_LEAD_WEEKS = 6  # assumed until she fills the Stock & Orders sheet
PREVIEW_DAYS = 14  # this close, a shipment can no longer arrive in time
ORDER_SOON_DAYS = 14
CLEARANCE_DAYS = 14
MIN_UPLIFT = 1.5
MAX_LIFTS = 2
SOLID_WINDOWS = 4  # past festivals needed to call a lift "solid"
CASH_STAGES = ('later', 'order_soon', 'order_now')  # while there is time to order
DAYS_PER_WEEK = 7

PLAYBOOK_FOOTER = (
    'A planning guide from past sales, not financial advice. Lunar festival '
    'dates are estimates, so check them.'
)


@dataclass(frozen=True)
class Lift:
    category: str
    uplift: float
    windows: int
    confidence: str  # 'solid' | 'rough'


@dataclass(frozen=True)
class PlaybookItem:
    festival: str
    start: pd.Timestamp
    end: pd.Timestamp
    stage: str  # later | order_soon | order_now | preview | on_now | clearance
    order_by: pd.Timestamp
    lead_weeks: int
    lead_assumed: bool
    lifts: Tuple[Lift, ...]
    text: str
    cash: Optional[CashPlan] = None


def lead_weeks_from_stock(stock: Optional[pd.DataFrame]) -> Tuple[int, bool]:
    """(weeks a shipment takes, whether that is only an assumption)."""
    if stock is None or stock['weeks_to_arrive'].dropna().empty:
        return DEFAULT_LEAD_WEEKS, True
    return int(round(stock['weeks_to_arrive'].median())), False


def _stage(window, today: pd.Timestamp, order_by: pd.Timestamp) -> Optional[str]:
    if window.end < today:
        return 'clearance' if (today - window.end).days <= CLEARANCE_DAYS else None
    if window.start <= today:
        return 'on_now'
    days_to_start = (window.start - today).days
    if days_to_start > HORIZON_DAYS:
        return None
    if days_to_start <= PREVIEW_DAYS:
        return 'preview'
    if today >= order_by:
        return 'order_now'
    if (order_by - today).days <= ORDER_SOON_DAYS:
        return 'order_soon'
    return 'later'


def _lifts(uplift: pd.DataFrame, festival: str) -> Tuple[Lift, ...]:
    rows = uplift[(uplift['festival'] == festival) & (uplift['uplift'] >= MIN_UPLIFT)]
    rows = rows.sort_values('uplift', ascending=False).head(MAX_LIFTS)
    return tuple(
        Lift(str(r['category']), float(r['uplift']), int(r['windows']),
             'solid' if r['windows'] >= SOLID_WINDOWS else 'rough')
        for r in rows.to_dict('records')
    )


def _lift_text(festival: str, lifts: Tuple[Lift, ...], uplift: pd.DataFrame) -> str:
    if lifts:
        parts = [f'{lift.category} about {lift.uplift:.1f}x ({lift.confidence}, '
                 f'{lift.windows} past years)' for lift in lifts]
        return ' Usually sells faster: ' + ', '.join(parts) + '.'
    if (uplift['festival'] == festival).any():
        return ''
    return ' No past sales to learn from, so there is no lift figure.'


def _lead_text(lead_weeks: int, assumed: bool) -> str:
    text = f'a shipment takes {lead_weeks} weeks'
    if assumed:
        text += ' (an assumption: add your real time to the Stock & Orders sheet)'
    return text


def _advice(stage: str, order_by: pd.Timestamp, lead: str) -> str:
    if stage == 'later':
        return f'Order by {order_by:%d %b}; {lead}.'
    if stage == 'order_soon':
        return f'Order soon, by {order_by:%d %b}; {lead}.'
    if stage == 'order_now':
        return f'Order now. The order-by date was {order_by:%d %b}; {lead}.'
    if stage == 'preview':
        return ('Too late to order a shipment in time; sell what is in stock '
                'and tell regulars what is coming.')
    if stage == 'on_now':
        return 'On now. Keep an eye on stock and promote the best sellers.'
    return 'Just finished. Sell down leftover stock over the next two weeks.'


def _cash_text(plan: CashPlan) -> str:
    text = f' Expected sales in the window: about {money(plan.expected)}'
    if plan.low is not None and plan.high is not None:
        text += (f' (likely {money(plan.low)} to {money(plan.high)}, from '
                 f'{plan.backtest_windows} past festivals).')
    else:
        text += ' (not enough history for a range).'
    if plan.budget_expected is None:
        return text
    if plan.budget_low is None:
        return text + f' A stock budget at that level is about {money(plan.budget_expected)}.'
    return text + (f' A cautious stock budget is about {money(plan.budget_low)} '
                   f'to {money(plan.budget_expected)}.')


def _item(window, uplift, today, lead_weeks, assumed, cash_for) -> Optional[PlaybookItem]:
    order_by = window.start - pd.Timedelta(days=lead_weeks * DAYS_PER_WEEK)
    stage = _stage(window, today, order_by)
    if stage is None:
        return None
    lifts = _lifts(uplift, window.name)
    lead = _lead_text(lead_weeks, assumed)
    cash = cash_for(window) if cash_for and stage in CASH_STAGES else None
    text = (f'{window.name} ({window.start:%d %b} to {window.end:%d %b}): '
            f'{_advice(stage, order_by, lead)}'
            f'{_lift_text(window.name, lifts, uplift)}'
            f'{_cash_text(cash) if cash else ""}')
    return PlaybookItem(window.name, window.start, window.end, stage, order_by,
                        lead_weeks, assumed, lifts, text, cash)


def build_playbook(calendar, uplift: pd.DataFrame, today, lead_weeks: int,
                   lead_assumed: bool,
                   cash_for: Optional[Callable] = None) -> Tuple[PlaybookItem, ...]:
    """One item per festival that is coming up, on now, or just finished.

    ``cash_for(window)`` gives the window's CashPlan (or None); it is asked
    only while there is still time to place an order.
    """
    today = pd.Timestamp(today).normalize()
    items = (_item(w, uplift, today, lead_weeks, lead_assumed, cash_for)
             for w in sorted(calendar, key=lambda w: w.start))
    return tuple(item for item in items if item is not None)
