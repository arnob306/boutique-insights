"""
Repeat customers who are due a nudge.

This is the one place real customer names are read. They come straight from
the workbook and go only into a text file under data/private/, never into
WorkbookData, the report, the dashboard, the email or the decision log. The
rest of the pipeline keeps working on hashed IDs.

A customer is "due" for DUE_WINDOW_DAYS once the days since their last purchase
reach their usual gap between purchases, so the list is fresh each week rather
than the same people for months. They count as "gone quiet" after LAPSE_FACTOR
times that gap. (On her real data most customers buy about once a year, so a
rule of "due until they lapse" listed hundreds of people.)
"""

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd

from src.adapters.boutique_xlsx import SALES_SHEET, SchemaError
from src.reports.weekly import money

MIN_GAP_DAYS = 30  # so someone who bought last week is not nudged again
LAPSE_FACTOR = 3
DUE_WINDOW_DAYS = 28  # how long after their due date a customer stays on the list
MAX_LINES = 15
MIN_PURCHASE_DAYS = 2

SOURCE_COLUMNS = {
    'Date': 'date',
    'Customer': 'name',
    'Suburb': 'suburb',
    'Category': 'category',
    'Quantity': 'quantity',
    'Sale Total (AUD)': 'amount',
}

PRIVACY_NOTE = (
    "This list has customers' names and buying habits. Keep it on your "
    'computer: do not forward it, email it or put it in a shared folder.'
)


@dataclass(frozen=True)
class ReminderLine:
    name: str
    suburb: Optional[str]  # only set when two customers share a name
    last_purchase: pd.Timestamp
    days_since: int
    usual_gap_days: int
    purchases: int
    top_category: Optional[str]
    spend: float


@dataclass(frozen=True)
class Reminders:
    due: Tuple[ReminderLine, ...]
    due_count: int  # everyone due, including those beyond the list cap
    quiet_count: int


def load_customer_rows(path) -> pd.DataFrame:
    """The Sales sheet's customer columns, with names, in standard names."""
    path = Path(path)
    try:
        raw = pd.read_excel(path, sheet_name=SALES_SHEET)
    except ValueError as exc:
        if 'Worksheet named' in str(exc):
            raise SchemaError(f"Sheet '{SALES_SHEET}' is missing from {path.name}.")
        raise
    missing = [c for c in SOURCE_COLUMNS if c not in raw.columns]
    if missing:
        raise SchemaError(
            f"Sheet '{SALES_SHEET}' is missing column(s): {', '.join(missing)}. "
            'Check the headers have not been renamed.')
    rows = raw[list(SOURCE_COLUMNS)].rename(columns=SOURCE_COLUMNS).copy()
    rows['date'] = pd.to_datetime(rows['date'], errors='coerce')
    rows['quantity'] = pd.to_numeric(rows['quantity'], errors='coerce').fillna(0)
    rows['amount'] = pd.to_numeric(rows['amount'], errors='coerce').fillna(0.0)
    return rows.dropna(subset=['date']).reset_index(drop=True)


def _key(value: object) -> str:
    return ' '.join(value.split()).casefold() if isinstance(value, str) else ''


def _commonest(values: pd.Series) -> Optional[str]:
    cleaned = [' '.join(v.split()) for v in values if isinstance(v, str) and v.strip()]
    return Counter(cleaned).most_common(1)[0][0] if cleaned else None


def _top_category(group: pd.DataFrame) -> Optional[str]:
    spend = group.dropna(subset=['category']).groupby('category')['amount'].sum()
    return None if spend.empty else str(spend.idxmax())


def _customer_line(group: pd.DataFrame, today: pd.Timestamp):
    """(line, suburb) for one repeat customer, or None for a one-off."""
    days = sorted(group['day'].unique())
    if len(days) < MIN_PURCHASE_DAYS:
        return None
    gaps = pd.Series(days).diff().dropna().dt.days
    last = pd.Timestamp(days[-1])
    line = ReminderLine(
        name=_commonest(group['name']), suburb=None, last_purchase=last,
        days_since=int((today - last).days),
        usual_gap_days=max(int(round(gaps.median())), MIN_GAP_DAYS),
        purchases=len(days), top_category=_top_category(group),
        spend=float(group['amount'].sum()))
    return line, _commonest(group['suburb'])


def _repeat_customers(rows: pd.DataFrame, today: pd.Timestamp) -> list:
    buys = rows[(rows['quantity'] > 0) & rows['name'].map(_key).ne('')].copy()
    buys['name_key'] = buys['name'].map(_key)
    buys['suburb_key'] = buys['suburb'].map(_key)
    buys['day'] = buys['date'].dt.normalize()
    found = (_customer_line(group, today)
             for _, group in buys.groupby(['name_key', 'suburb_key']))
    return [item for item in found if item is not None]


def _with_suburbs(found: list) -> list:
    """Show a suburb only where two repeat customers share a name."""
    counts = Counter(_key(line.name) for line, _ in found)
    return [replace(line, suburb=suburb if counts[_key(line.name)] > 1 else None)
            for line, suburb in found]


def build_reminders(rows: pd.DataFrame, today) -> Reminders:
    """Who is due a nudge, biggest spenders first, capped at MAX_LINES."""
    today = pd.Timestamp(today).normalize()
    lines = _with_suburbs(_repeat_customers(rows, today))
    due = [l for l in lines
           if 0 <= l.days_since - l.usual_gap_days <= DUE_WINDOW_DAYS]
    quiet = [l for l in lines if l.days_since > LAPSE_FACTOR * l.usual_gap_days]
    due.sort(key=lambda l: (-l.spend, l.name))
    return Reminders(tuple(due[:MAX_LINES]), len(due), len(quiet))


def _days_ago(days: int) -> str:
    return 'yesterday' if days == 1 else f'{days} days ago'


def _line_text(l: ReminderLine) -> str:
    who = f'{l.name} ({l.suburb})' if l.suburb else l.name
    text = (f'{who}: last bought {l.last_purchase:%d %b %Y} ({_days_ago(l.days_since)}), '
            f'usually every {l.usual_gap_days} days ({l.purchases} purchases).')
    if l.top_category:
        text += f' Usually buys {l.top_category}.'
    return text + f' Spent {money(l.spend)} in total.'


def render_reminders(result: Reminders, today) -> str:
    """The text file she reads."""
    today = pd.Timestamp(today)
    lines = [f'Customers to nudge - {today:%d %b %Y}', PRIVACY_NOTE, '']
    if result.due_count == 0:
        lines.append('Nobody is due a nudge this week.')
    else:
        noun = 'repeat customer is' if result.due_count == 1 else 'repeat customers are'
        head = f'{result.due_count} {noun} due a nudge'
        if result.due_count > len(result.due):
            head += f' (the {len(result.due)} who have spent most are listed)'
        lines += [head + '.', '']
        lines += [f'- {_line_text(l)}' for l in result.due]
    if result.quiet_count:
        lines += ['', f'{result.quiet_count} more have gone quiet (nothing bought for '
                  f'over {LAPSE_FACTOR} times their usual gap) and are not listed.']
    return '\n'.join(lines) + '\n'


def write_reminders(result: Reminders, out_dir: Path, today) -> Path:
    """Write the list to ``out_dir`` and return its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f'reminders_{pd.Timestamp(today):%Y-%m-%d}.txt'
    path.write_text(render_reminders(result, today), encoding='utf-8')
    return path
