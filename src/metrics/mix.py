"""
Where the money comes from: category and channel mix over the last 12 months.

Each line is a share of net sales (refunds subtract), compared with the 12
months before when there is data for that. Margin uses only rows that have a
cost. This is plain arithmetic for the report; it does not predict anything.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

import pandas as pd

PERIOD_DAYS = 364
MIN_SHIFT = 0.05  # a move of 5 points or more is worth mentioning
MIN_BEST_MARGIN_SHARE = 0.02  # ignore tiny categories when naming the best margin
MAX_NOTES = 4
DAY = pd.Timedelta(days=1)


@dataclass(frozen=True)
class CategoryLine:
    category: str
    revenue_share: float
    margin: Optional[float]
    share_change: Optional[float]  # points, as a fraction; None without an earlier year


@dataclass(frozen=True)
class ChannelLine:
    channel: str
    share: float
    share_change: Optional[float]


def _period(sales: pd.DataFrame, as_of: pd.Timestamp, back: int) -> pd.DataFrame:
    """Sales in the 364 days ending ``back`` periods before ``as_of``."""
    end = as_of - back * PERIOD_DAYS * DAY
    day = sales['date'].dt.normalize()
    return sales[(day > end - PERIOD_DAYS * DAY) & (day <= end)]


def _shares(frame: pd.DataFrame, key: str) -> Optional[pd.Series]:
    """Each group's share of net sales, or None when there are no sales."""
    known = frame[frame[key].notna()]
    net = known.groupby(key)['line_total'].sum()
    total = net.sum()
    return net / total if total > 0 else None


def _change(now: pd.Series, before: Optional[pd.Series], name: str) -> Optional[float]:
    return None if before is None else float(now[name] - before.get(name, 0.0))


def _margin(frame: pd.DataFrame) -> Optional[float]:
    costed = frame[frame['unit_cost'].notna()]
    revenue = costed['line_total'].sum()
    if revenue <= 0:
        return None
    return float((revenue - (costed['quantity'] * costed['unit_cost']).sum()) / revenue)


def category_mix(sales: pd.DataFrame, as_of) -> Tuple[CategoryLine, ...]:
    """Categories by share of the last 12 months' sales, biggest first."""
    as_of = pd.Timestamp(as_of).normalize()
    now_frame, before_frame = _period(sales, as_of, 0), _period(sales, as_of, 1)
    now = _shares(now_frame, 'category')
    if now is None:
        return ()
    before = _shares(before_frame, 'category')
    lines = [
        CategoryLine(name, float(share),
                     _margin(now_frame[now_frame['category'] == name]),
                     _change(now, before, name))
        for name, share in now.items()
    ]
    return tuple(sorted(lines, key=lambda l: (-l.revenue_share, l.category)))


def channel_mix(sales: pd.DataFrame, as_of) -> Tuple[ChannelLine, ...]:
    """Sales channels by share of the last 12 months' sales, biggest first."""
    as_of = pd.Timestamp(as_of).normalize()
    now = _shares(_period(sales, as_of, 0), 'channel')
    if now is None:
        return ()
    before = _shares(_period(sales, as_of, 1), 'channel')
    lines = [ChannelLine(name, float(share), _change(now, before, name))
             for name, share in now.items()]
    return tuple(sorted(lines, key=lambda l: (-l.share, l.channel)))


def _points(change: float) -> str:
    return f'{abs(change) * 100:.0f} points {"up" if change > 0 else "down"}'


def _category_notes(categories: Tuple[CategoryLine, ...]) -> list:
    if not categories:
        return []
    top = categories[0]
    text = f'{top.category} brings in {top.revenue_share:.0%} of sales'
    if top.margin is not None:
        text += f' at a {top.margin:.0%} margin'
    notes = [text + '.']
    rivals = [c for c in categories[1:]
              if c.margin is not None and c.revenue_share >= MIN_BEST_MARGIN_SHARE
              and c.margin > (top.margin if top.margin is not None else -1.0)]
    if rivals:
        best = max(rivals, key=lambda c: c.margin)
        notes.append(f'{best.category} earns the best margin ({best.margin:.0%}) '
                     f'but is only {best.revenue_share:.0%} of sales.')
    return notes


def _channel_notes(channels: Tuple[ChannelLine, ...]) -> list:
    if not channels:
        return []
    lead = channels[0]
    text = f'{lead.channel} is the biggest channel at {lead.share:.0%} of sales'
    if lead.share_change is not None and abs(lead.share_change) >= MIN_SHIFT:
        text += f', {_points(lead.share_change)} on the year before'
    notes = [text + '.']
    movers = [c for c in channels[1:]
              if c.share_change is not None and abs(c.share_change) >= MIN_SHIFT]
    if movers:
        mover = max(movers, key=lambda c: abs(c.share_change))
        notes.append(f'{mover.channel} is {_points(mover.share_change)} on the year '
                     f'before, at {mover.share:.0%} of sales.')
    return notes


def mix_notes(categories: Tuple[CategoryLine, ...],
              channels: Tuple[ChannelLine, ...]) -> Tuple[str, ...]:
    """A few plain-English lines about the mix."""
    return tuple((_category_notes(categories) + _channel_notes(channels))[:MAX_NOTES])
