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
FESTIVAL_LIST_DAYS = 35  # list last year's festival buyers this far ahead
RECENT_BUYER_DAYS = 28  # someone who just shopped is left out
MATCH_TOLERANCE_DAYS = 60  # how far from "a year ago" a past window may start
YEAR_DAYS = 364  # 52 weeks, so weekdays line up

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


@dataclass(frozen=True)
class FestivalRegular:
    name: str
    suburb: Optional[str]  # only set when two customers share a name
    top_category: Optional[str]
    spend: float  # spent in last year's window
    festivals_attended: int  # past windows of this festival they bought in


@dataclass(frozen=True)
class FestivalList:
    festival: str
    start: pd.Timestamp
    end: pd.Timestamp
    last_start: pd.Timestamp  # the window last year that the list comes from
    last_end: pd.Timestamp
    regulars: Tuple[FestivalRegular, ...]
    count: int  # everyone who qualifies, including those beyond the list cap


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


def _named_purchases(rows: pd.DataFrame) -> pd.DataFrame:
    """Positive sales with a customer name, plus the keys that identify them."""
    buys = rows[(rows['quantity'] > 0) & rows['name'].map(_key).ne('')].copy()
    buys['name_key'] = buys['name'].map(_key)
    buys['suburb_key'] = buys['suburb'].map(_key)
    buys['day'] = pd.to_datetime(buys['date']).dt.normalize()  # also fine when empty
    return buys


def _repeat_customers(rows: pd.DataFrame, today: pd.Timestamp) -> list:
    buys = _named_purchases(rows)
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


def _last_year_window(window, past_windows: pd.DataFrame):
    """The same festival's past window that starts nearest a year earlier."""
    if past_windows.empty:
        return None
    same =past_windows[(past_windows['festival'] == window.name)
                        & (past_windows['end'] < window.start)]
    anchor = window.start - pd.Timedelta(days=YEAR_DAYS)
    gap = (same['start'] - anchor).abs().dt.days
    close = same[gap <= MATCH_TOLERANCE_DAYS]
    if close.empty:
        return None
    return close.loc[gap[close.index].idxmin()]


def _bought_in(buys: pd.DataFrame, start, end) -> pd.DataFrame:
    return buys[(buys['day'] >= pd.Timestamp(start).normalize())
                & (buys['day'] <= pd.Timestamp(end).normalize())]


def _festival_regulars(window_buys, attended: Counter, skip: set) -> list:
    regulars = []
    for key, group in window_buys.groupby(['name_key', 'suburb_key']):
        if key in skip:
            continue
        regulars.append(FestivalRegular(
            name=_commonest(group['name']), suburb=_commonest(group['suburb']),
            top_category=_top_category(group), spend=float(group['amount'].sum()),
            festivals_attended=attended[key]))
    shared = Counter(_key(r.name) for r in regulars)
    regulars = [replace(r, suburb=r.suburb if shared[_key(r.name)] > 1 else None)
                for r in regulars]
    return sorted(regulars, key=lambda r: (-r.spend, r.name))


def _festival_list(window, last, buys, past_windows, today) -> Optional[FestivalList]:
    recent = _bought_in(buys, today - pd.Timedelta(days=RECENT_BUYER_DAYS), today)
    skip = set(zip(recent['name_key'], recent['suburb_key']))
    attended = Counter()
    for _, past in past_windows[(past_windows['festival'] == window.name)
                                & (past_windows['end'] < window.start)].iterrows():
        seen = _bought_in(buys, past['start'], past['end'])
        attended.update(set(zip(seen['name_key'], seen['suburb_key'])))
    regulars = _festival_regulars(_bought_in(buys, last['start'], last['end']),
                                  attended, skip)
    if not regulars:
        return None
    return FestivalList(window.name, window.start, window.end, last['start'],
                        last['end'], tuple(regulars[:MAX_LINES]), len(regulars))


def build_festival_reminders(rows: pd.DataFrame, calendar, past_windows: pd.DataFrame,
                             today) -> Tuple[FestivalList, ...]:
    """For each festival starting soon, who bought in the same window last year
    and has not shopped lately. Soonest festival first."""
    today = pd.Timestamp(today).normalize()
    buys = _named_purchases(rows)
    lists = []
    for window in sorted(calendar, key=lambda w: w.start):
        if not 0 <= (window.start - today).days <= FESTIVAL_LIST_DAYS:
            continue
        last = _last_year_window(window, past_windows)
        if last is None:
            continue
        found = _festival_list(window, last, buys, past_windows, today)
        if found is not None:
            lists.append(found)
    return tuple(lists)


def _days_ago(days: int) -> str:
    return 'yesterday' if days == 1 else f'{days} days ago'


def _line_text(l: ReminderLine) -> str:
    who = f'{l.name} ({l.suburb})' if l.suburb else l.name
    text = (f'{who}: last bought {l.last_purchase:%d %b %Y} ({_days_ago(l.days_since)}), '
            f'usually every {l.usual_gap_days} days ({l.purchases} purchases).')
    if l.top_category:
        text += f' Usually buys {l.top_category}.'
    return text + f' Spent {money(l.spend)} in total.'


def _regular_text(r: FestivalRegular) -> str:
    who = f'{r.name} ({r.suburb})' if r.suburb else r.name
    times = 'once' if r.festivals_attended == 1 else f'{r.festivals_attended} times'
    text = f'{who}: spent {money(r.spend)} in that window last year'
    if r.top_category:
        text += f', mostly {r.top_category}'
    return text + f'. Has bought at this festival {times}.'


def _festival_block(item: FestivalList) -> list:
    noun = 'customer' if item.count == 1 else 'customers'
    head = (f'{item.festival} starts {item.start:%d %b}. {item.count} {noun} bought in '
            f'the same window last year ({item.last_start:%d %b} to '
            f'{item.last_end:%d %b %Y}) and have not shopped lately')
    if item.count > len(item.regulars):
        head += f' (the {len(item.regulars)} who spent most are listed)'
    return [head + '.', ''] + [f'- {_regular_text(r)}' for r in item.regulars] + ['']


def _gap_section(result: Reminders) -> list:
    lines = []
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
    return lines


def render_reminders(result: Reminders, today, festival_lists=()) -> str:
    """The text file she reads."""
    today = pd.Timestamp(today)
    lines = [f'Customers to nudge - {today:%d %b %Y}', PRIVACY_NOTE, '']
    for item in festival_lists:
        lines += _festival_block(item)
    if festival_lists:
        lines += ['Repeat customers due by their usual gap', '']
    return '\n'.join(lines + _gap_section(result)) + '\n'


def write_reminders(result: Reminders, out_dir: Path, today,
                    festival_lists=()) -> Path:
    """Write the list to ``out_dir`` and return its path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f'reminders_{pd.Timestamp(today):%Y-%m-%d}.txt'
    path.write_text(render_reminders(result, today, festival_lists), encoding='utf-8')
    return path
