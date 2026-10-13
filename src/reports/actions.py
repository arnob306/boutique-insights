"""
What to do this week, most urgent first.

One list shared by the email and the dashboard, so the two cannot disagree.
It holds counts and product names only, never customer details.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

from src.reports.formatting import festival_reminder_line, reminder_text
from src.reports.playbook import PlaybookItem

if TYPE_CHECKING:
    from src.reports.weekly import ReorderLine, WeeklyReport

URGENCY_RANK = {'now': 0, 'soon': 1, 'info': 2}
URGENCY_WORD = {'now': 'Now', 'soon': 'Soon', 'info': 'FYI'}


@dataclass(frozen=True)
class Action:
    urgency: str  # now | soon | info
    text: str


def action_line(action: Action) -> str:
    """One action as a plain sentence that starts with how urgent it is."""
    return f'{URGENCY_WORD[action.urgency]}: {action.text}'


def weeks_text(count: float) -> str:
    whole = round(count)
    return f'{whole} week' if whole == 1 else f'{whole} weeks'


def cover_weeks(line: 'ReorderLine') -> Optional[float]:
    return line.stock_now / line.units_per_week if line.units_per_week > 0 else None


def festival_action(item: PlaybookItem) -> Optional[Action]:
    name, order_by = item.festival, f'{item.order_by:%d %b}'
    if item.stage == 'order_now':
        return Action('now', f'{name}: order now. The order-by date was {order_by}.')
    if item.stage == 'order_soon':
        return Action('soon', f'{name}: order by {order_by}.')
    if item.stage == 'preview':
        return Action('info', f'{name}: too late to order a shipment in time. '
                              'Sell what is in stock.')
    if item.stage == 'on_now':
        return Action('info', f'{name} is on. Keep an eye on stock.')
    return None


def reorder_action(line: 'ReorderLine') -> Action:
    cover = cover_weeks(line)
    head = f'Order {line.order_qty} {line.product}: '
    if cover is None:
        return Action('soon', head + 'no recent sales, so treat this as a rough guess.')
    if cover < line.lead_weeks:
        # Whole weeks would hide a near miss ("4 weeks, before a 4-week shipment").
        left = f'{cover:.1f} weeks' if round(cover) >= line.lead_weeks else weeks_text(cover)
        return Action('now', head + f'stock runs out in {left}, before a '
                                    f'shipment ({weeks_text(line.lead_weeks)}) arrives.')
    return Action('soon', head + f'stock lasts {weeks_text(cover)}.')


def build_actions(r: 'WeeklyReport') -> List[Action]:
    """Every action for the week, most urgent first."""
    found = [a for a in map(festival_action, r.playbook) if a]
    found += [reorder_action(line) for line in r.reorder]
    if r.reminder_count:
        found.append(Action('info', reminder_text(r.reminder_count)))
    found += [Action('info', festival_reminder_line(f, n))
              for f, n in r.festival_reminder_counts if n > 0]
    return sorted(found, key=lambda a: URGENCY_RANK[a.urgency])
