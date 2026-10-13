"""
A one-page, phone-friendly dashboard.

Plain HTML and inline SVG: no scripts, no external links, no images, no network
calls, so it can be opened from a file or an email attachment. It shows the
same figures as the weekly summary (built from the same ``WeeklyReport``) plus
a weekly sales chart. Only totals are drawn; no customer details are available
here.

It is organised around the questions she opens it with, in this order:

1. Is the week healthy?          status tiles
2. What should I do?             "Do this week", most urgent first
3. What changed?                 sales chart, usual-week line, last 4 weeks
4. Where is the risk?            reorder cover and festival cards
5. Where does the money come from?  profit by product, mix and channels

The few in-page jump links are plain anchors (no scripts). The look is in
``dashboard_style.py``.
"""

import html
from typing import Iterable, List, Optional, Tuple

import pandas as pd

from src.metrics.cashplan import CashPlan
from src.reports.actions import Action, build_actions, cover_weeks, weeks_text
from src.reports.dashboard_style import CSS
from src.reports.playbook import PLAYBOOK_FOOTER, PlaybookItem
from src.reports.weekly import (
    UNTRUSTED_BANNER,
    MoneyLine,
    ReorderLine,
    WeeklyReport,
    festival_reminder_line,
    money,
    money_bullet,
    reminder_text,
)

WEEKS_SHOWN = 26
MAX_ACTIONS = 6
CHART_WIDTH = 360
PLOT_HEIGHT = 120
CHART_HEIGHT = 150
LABEL_ROOM = 16  # space above the tallest bar for its value label
MAX_BAR_WIDTH = 24
BAR_GAP = 3
MIN_BAR_HEIGHT = 2
MIN_MONTH_GAP = 34  # keeps month names from touching
MIN_LABEL_GAP_WEEKS = 4
MIN_FILL_PERCENT = 2.0
METER_HEADROOM = 1.25
RANGE_HEADROOM = 1.1  # room to the right of a festival's high estimate
RECENT_WEEKS = 4

_PILL_WORD = {'now': 'Now', 'soon': 'Soon', 'info': 'FYI'}
_STAGE_WORD = {
    'later': 'Later', 'order_soon': 'Order soon', 'order_now': 'Order now',
    'preview': 'Too late to order', 'on_now': 'On now', 'clearance': 'Just finished',
}
_STAGE_TONE = {'order_now': 'now', 'order_soon': 'soon'}


def _esc(value: object) -> str:
    return html.escape(str(value))


# --- status tiles: is the week healthy? ---------------------------------------

def _trend(r: WeeklyReport) -> Optional[Tuple[str, str, str]]:
    """(direction, value, caption) against a usual week, or None."""
    if r.usual_week_sales is None:
        return None
    gap = r.sales_this_week - r.usual_week_sales
    if round(abs(gap)) == 0:
        return 'flat', '= Same', 'as a usual week'
    if gap > 0:
        return 'up', f'▲ {money(gap)}', 'more than a usual week'
    return 'down', f'▼ {money(-gap)}', 'less than a usual week'


def _tile(label: str, value: str, caption: str, extra: str = '', css: str = '') -> str:
    sub = f'<p class="tile-sub">{_esc(caption)}</p>' if caption else ''
    return (f'<div class="tile{css}"{extra}><p class="tile-label">{_esc(label)}</p>'
            f'<p class="tile-value">{_esc(value)}</p>{sub}</div>')


def _tiles(r: WeeklyReport) -> str:
    last_year = (f'Same week last year: {money(r.sales_last_year)}'
                 if r.sales_last_year is not None else '')
    tiles = [_tile('Sold in the last 7 days', money(r.sales_this_week), last_year,
                   css=' main')]
    trend = _trend(r)
    if trend:
        direction, value, caption = trend
        tiles.append(_tile('This week vs usual', value, caption,
                           extra=f' data-trend="{direction}"', css=f' {direction}'))
    if r.profit_this_week is not None or r.overall_margin is not None:
        profit = money(r.profit_this_week) if r.profit_this_week is not None else 'n/a'
        margin = (f'Margin over the last 12 months: {r.overall_margin * 100:.0f}%'
                  if r.overall_margin is not None else '')
        tiles.append(_tile('Estimated profit this week', profit, margin))
    return f'<div class="tiles">{"".join(tiles)}</div>'


# --- do this week: what should I do? ----------------------------------------------

def _action_row(action: Action) -> str:
    return (f'<li class="action" data-urgency="{action.urgency}">'
            f'<span class="pill {action.urgency}">{_PILL_WORD[action.urgency]}</span>'
            f'<span class="action-text">{_esc(action.text)}</span></li>')


def _actions_body(r: WeeklyReport) -> str:
    found = build_actions(r)
    if not found:
        text = 'Nothing urgent this week.'
        if r.reorder_status == 'no_stock_sheet':
            text += ' Reorder advice is off until the Stock &amp; Orders sheet is added.'
        return f'<p class="quiet">{text}</p>'
    rows = ''.join(_action_row(a) for a in found[:MAX_ACTIONS])
    more = len(found) - MAX_ACTIONS
    tail = (f'<p class="note">{more} more in the sections below.</p>'
            if more > 0 else '')
    return f'<ul class="actions">{rows}</ul>{tail}'


# --- sales: what changed? -------------------------------------------------------

def _bar(index: int, week, value: float, top: float, slot: float, latest: bool) -> str:
    width = min(MAX_BAR_WIDTH, max(slot - BAR_GAP, 1))
    x = index * slot + (slot - width) / 2
    height = max(MIN_BAR_HEIGHT, value / top * (PLOT_HEIGHT - LABEL_ROOM)) if value > 0 else 0.0
    css = 'bar latest' if latest else 'bar'
    return (
        f'<rect class="{css}" data-week="{week:%Y-%m-%d}" x="{x:.1f}" '
        f'y="{PLOT_HEIGHT - height:.1f}" width="{width:.1f}" height="{height:.1f}" '
        f'rx="2"><title>Week of {week:%d %b}: {_esc(money(value))}</title></rect>'
    )


def _value_label(index: int, value: float, top: float, slot: float, text: str) -> str:
    height = value / top * (PLOT_HEIGHT - LABEL_ROOM)
    x = min(max(index * slot + slot / 2, 24), CHART_WIDTH - 24)
    return (f'<text class="val" x="{x:.1f}" y="{PLOT_HEIGHT - height - 5:.1f}" '
            f'text-anchor="middle">{_esc(text)}</text>')


def _month_ticks(weeks: pd.DatetimeIndex, slot: float) -> str:
    ticks: list[str] = []
    last_x: float = -MIN_MONTH_GAP
    last_month: Optional[int] = None
    for i, week in enumerate(weeks):
        if week.month != last_month and i * slot - last_x >= MIN_MONTH_GAP:
            ticks.append(f'<text class="tick" x="{i * slot:.1f}" '
                         f'y="{CHART_HEIGHT - 6}">{week:%b}</text>')
            last_x = i * slot
        last_month = week.month
    return ''.join(ticks)


def _chart_labels(values: list, top: float, slot: float) -> str:
    newest = len(values) - 1
    labels = [_value_label(newest, values[newest], top, slot, money(values[newest]))]
    best = max(range(len(values)), key=values.__getitem__)
    if values[best] > 0 and newest - best >= MIN_LABEL_GAP_WEEKS:
        labels.append(_value_label(best, values[best], top, slot, money(values[best])))
    return ''.join(labels)


def _usual_line(usual: Optional[float], top: float) -> str:
    """A dashed line at a usual week's sales, so each bar reads as above or below."""
    if not usual or usual <= 0:
        return ''
    y = PLOT_HEIGHT - usual / top * (PLOT_HEIGHT - LABEL_ROOM)
    return (f'<line class="usual" data-usual-week="{usual:.0f}" x1="0" '
            f'x2="{CHART_WIDTH}" y1="{y:.1f}" y2="{y:.1f}"/>'
            f'<text class="usual-label" x="2" y="{y - 3:.1f}">'
            f'usual {_esc(money(usual))}</text>')


def _four_week_line(series: pd.DataFrame) -> str:
    """The last 4 weeks against the 4 before, in words; empty if it cannot be said."""
    if len(series) < 2 * RECENT_WEEKS:
        return ''
    sales = series['sales'].astype(float)
    recent = float(sales.iloc[-RECENT_WEEKS:].sum())
    before = float(sales.iloc[-2 * RECENT_WEEKS:-RECENT_WEEKS].sum())
    if before <= 0:
        return ''
    change = (recent - before) / before
    if round(abs(change) * 100) == 0:
        move = 'about the same as'
    else:
        move = f'{"up" if change > 0 else "down"} {abs(change) * 100:.0f}% on'
    return (f'<p class="change">Last {RECENT_WEEKS} weeks: {_esc(money(recent))}, '
            f'{move} the {RECENT_WEEKS} weeks before.</p>')


def _sales_chart(series: pd.DataFrame, usual: Optional[float]) -> str:
    count = len(series)
    if count == 0:
        return '<p>No sales to chart yet.</p>'
    values = [float(v) for v in series['sales']]
    slot = CHART_WIDTH / count
    top = max(max(values), usual or 0.0, 1.0)
    bars = ''.join(
        _bar(i, week, value, top, slot, latest=i == count - 1)
        for i, (week, value) in enumerate(zip(series.index, values, strict=True))
    )
    label = f'Weekly sales for the last {count} weeks, best week {money(max(values))}'
    return (
        f'{_four_week_line(series)}'
        f'<svg viewBox="0 0 {CHART_WIDTH} {CHART_HEIGHT}" role="img" '
        f'aria-label="{_esc(label)}">{bars}'
        f'<line class="axis" x1="0" x2="{CHART_WIDTH}" y1="{PLOT_HEIGHT}" '
        f'y2="{PLOT_HEIGHT}"/>{_usual_line(usual, top)}'
        f'{_month_ticks(pd.DatetimeIndex(series.index), slot)}'
        f'{_chart_labels(values, top, slot)}</svg>'
        '<p class="note">One bar per week. The amber bar is the last 7 days; '
        'the dashed line is a usual week.</p>'
        f'{_table_view(series)}'
    )


def _table_view(series: pd.DataFrame) -> str:
    rows = ''.join(
        f'<tr><td>{week:%d %b %Y}</td><td>{_esc(money(row.sales))}</td>'
        f'<td>{_esc(money(row.profit))}</td></tr>'
        for week, row in series.iloc[::-1].iterrows()
    )
    return (
        '<details><summary>Show as a table</summary><table><thead><tr>'
        '<th scope="col">Week starting</th><th scope="col">Sales</th>'
        '<th scope="col">Profit</th></tr></thead>'
        f'<tbody>{rows}</tbody></table></details>'
    )


# --- reorder: stock cover against shipment time -----------------------------------

def _meter(cover: float, lead: int) -> str:
    scale = max(cover, lead) * METER_HEADROOM or 1.0
    short = ' short' if cover < lead else ''
    return (
        f'<div class="meter"><div class="meter-fill{short}" '
        f'style="width:{cover / scale * 100:.0f}%"></div>'
        f'<div class="meter-mark" style="left:{lead / scale * 100:.0f}%"></div></div>'
    )


def _order_text(line: ReorderLine, cover: Optional[float]) -> str:
    if cover is None:
        return f'{line.stock_now} left and no recent sales.'
    text = (f'{line.stock_now} left, selling about {line.units_per_week:.1f} a week. '
            f'That lasts {weeks_text(cover)}; a shipment takes {weeks_text(line.lead_weeks)}.')
    if line.on_order:
        text += f' {line.on_order} already on order.'
    if line.note:
        text += f' {line.note}.'
    if line.confidence == 'low':
        text += ' Not many recent sales, so treat this as a rough guess.'
    return text


def _order_row(line: ReorderLine) -> str:
    cover = cover_weeks(line)
    attrs = f'data-reorder="{_esc(line.product)}"'
    meter = ''
    if cover is not None:
        attrs += f' data-cover-weeks="{cover:.1f}" data-lead-weeks="{line.lead_weeks}"'
        meter = _meter(cover, line.lead_weeks)
    return (
        f'<li class="order" {attrs}><div class="order-head">'
        f'<span class="order-qty">Order {line.order_qty}</span>'
        f'<span class="order-name">{_esc(line.product)}</span></div>'
        f'<p class="order-text">{_esc(_order_text(line, cover))}</p>{meter}</li>'
    )


def _reorder(r: WeeklyReport) -> str:
    if r.reorder_status == 'no_stock_sheet':
        return ("<p>Reorder advice is off. Add a 'Stock &amp; Orders' sheet to "
                'the workbook and it will switch on.</p>')
    if r.reorder_status == 'nothing':
        return '<p>Nothing needs ordering right now.</p>'
    note = ('<p class="note">The bar is how long stock lasts. The line marks '
            'when a new shipment would arrive; amber means stock runs out first.</p>')
    return f'<ul>{"".join(_order_row(line) for line in r.reorder)}</ul>{note}'


# --- festival cards ---------------------------------------------------------------

def _stage_advice(item: PlaybookItem) -> str:
    by = f'{item.order_by:%d %b}'
    return {
        'later': f'Order by {by}.',
        'order_soon': f'Order by {by}.',
        'order_now': f'Order now. The order-by date was {by}.',
        'preview': ('Too late to order a shipment in time. Sell what is in stock '
                    'and tell regulars what is coming.'),
        'on_now': 'On now. Keep an eye on stock and promote the best sellers.',
        'clearance': 'Just finished. Sell down leftover stock over the next two weeks.',
    }[item.stage]


def _lead_note(item: PlaybookItem) -> str:
    text = f'A shipment takes {weeks_text(item.lead_weeks)}'
    if item.lead_assumed:
        text += ' (an assumption: add your real time to the Stock &amp; Orders sheet)'
    return f'<p class="note">{text}.</p>'


def _lifts(item: PlaybookItem) -> str:
    if not item.lifts:
        return ''
    parts = ', '.join(
        f'{_esc(lift.category)} about {lift.uplift:.1f}x '
        f'({lift.confidence}, {lift.windows} past years)' for lift in item.lifts)
    return f'<p>Usually sells faster: {parts}.</p>'


def _range_bar(plan: CashPlan) -> str:
    if plan.low is None or plan.high is None:
        return ''
    scale = plan.high * RANGE_HEADROOM or 1.0
    left, width = plan.low / scale * 100, (plan.high - plan.low) / scale * 100
    mark = plan.expected / scale * 100
    return (f'<div class="range" data-range-low="{plan.low:.0f}" '
            f'data-range-high="{plan.high:.0f}"><div class="band" '
            f'style="left:{left:.1f}%;width:{width:.1f}%"></div>'
            f'<div class="mark" style="left:{mark:.1f}%"></div></div>')


def _budget_text(plan: CashPlan) -> str:
    if plan.budget_expected is None:
        return ''
    if plan.budget_low is None:
        return f' A stock budget at that level is about {money(plan.budget_expected)}.'
    return (f' A cautious stock budget is about {money(plan.budget_low)} '
            f'to {money(plan.budget_expected)}.')


def _cash_block(plan: CashPlan) -> str:
    text = f'Expected sales about {money(plan.expected)}'
    if plan.low is not None and plan.high is not None:
        text += (f', likely {money(plan.low)} to {money(plan.high)} '
                 f'(from {plan.backtest_windows} past festivals).')
    else:
        text += ' (not enough history for a range).'
    return (f'<div class="cash" data-expected="{plan.expected:.0f}">'
            f'<p>{_esc(text + _budget_text(plan))}</p>{_range_bar(plan)}</div>')


def _festival_card(item: PlaybookItem) -> str:
    tone = _STAGE_TONE.get(item.stage, 'info')
    cash = _cash_block(item.cash) if item.cash else ''
    return (
        f'<article class="card" data-festival="{_esc(item.festival)}">'
        f'<div class="card-head"><h3>{_esc(item.festival)}</h3>'
        f'<span class="pill {tone}">{_STAGE_WORD[item.stage]}</span></div>'
        f'<p class="card-dates">{item.start:%d %b} to {item.end:%d %b}</p>'
        f'<p>{_esc(_stage_advice(item))}</p>{_lead_note(item)}{_lifts(item)}{cash}'
        '</article>'
    )


def _playbook(r: WeeklyReport) -> str:
    if not r.playbook:
        return ''
    cards = ''.join(_festival_card(item) for item in r.playbook)
    return f'{cards}<p class="note">{_esc(PLAYBOOK_FOOTER)}</p>'


# --- customers, money and the data footer ----------------------------------------

def _profit_rows(lines: Iterable[MoneyLine]) -> str:
    lines = list(lines)
    if not lines:
        return '<p>No costed sales yet.</p>'
    scale = max(abs(line.profit) for line in lines) or 1.0
    rows = []
    for line in lines:
        width = max(MIN_FILL_PERCENT, abs(line.profit) / scale * 100)
        tone = ' loss' if line.profit < 0 else ''
        rows.append(
            f'<div class="row" data-product="{_esc(line.product)}">'
            f'<span class="row-text">{_esc(money_bullet(line))}</span>'
            f'<div class="track"><div class="fill{tone}" '
            f'style="width:{width:.0f}%"></div></div></div>'
        )
    return ''.join(rows)


def _list(items: Iterable[str]) -> str:
    entries = ''.join(f'<li>{_esc(item)}</li>' for item in items)
    return f'<ul class="plain">{entries}</ul>' if entries else ''


def _reminders(r: WeeklyReport) -> str:
    if r.reminder_count is None:
        return ''
    festivals = _list(festival_reminder_line(f, n)
                      for f, n in r.festival_reminder_counts)
    return f'<p>{_esc(reminder_text(r.reminder_count))}</p>{festivals}'


def _data_checks(r: WeeklyReport) -> str:
    if not r.data_notes:
        return ''
    return (f'<section id="data"><details><summary>Data checks '
            f'({len(r.data_notes)})</summary>{_list(r.data_notes)}</details></section>')


def _banner(r: WeeklyReport) -> str:
    return f'<p class="banner">{_esc(UNTRUSTED_BANNER)}</p>' if not r.trusted else ''


def _section(anchor: str, title: str, body: str) -> str:
    if not body:
        return ''
    return f'<section id="{anchor}"><h2>{_esc(title)}</h2>{body}</section>'


def _jump_links(sections: List[Tuple[str, str, str]]) -> str:
    """Anchors to the main sections that are on the page: (anchor, label, body)."""
    links = ''.join(f'<a href="#{anchor}">{label}</a>'
                    for anchor, label, body in sections if body)
    return f'<nav class="jump" aria-label="Jump to a section">{links}</nav>'


def render_dashboard(r: WeeklyReport, series: pd.DataFrame) -> str:
    """The whole dashboard as one self-contained HTML string."""
    earners = (*r.top_earners, *r.small_earners)
    main = [
        ('actions', 'Actions', 'Do this week', _actions_body(r)),
        ('sales', 'Sales', 'How sales are going', _sales_chart(series, r.usual_week_sales)),
        ('reorder', 'Reorder', 'Reorder this week', _reorder(r)),
        ('festivals', 'Festivals', 'Festival playbook', _playbook(r)),
        ('customers', 'Customers', 'Customers to nudge', _reminders(r)),
        ('money', 'Money', 'Profit by product (last 12 months)', _profit_rows(earners)),
    ]
    sections = ''.join(_section(anchor, title, body) for anchor, _, title, body in main)
    body = ''.join([
        '<h1>Boutique dashboard</h1>',
        f'<p class="when">Data through {_esc(f"{r.data_through:%a %d %b %Y}")}</p>',
        _banner(r),
        _jump_links([(anchor, label, text) for anchor, label, _, text in main]),
        _tiles(r),
        sections,
        _section('mix', 'Mix and channels (last 12 months)', _list(r.mix_notes)),
        _data_checks(r),
    ])
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<title>Boutique dashboard</title><style>{CSS}</style></head>'
        f'<body><div class="paar" aria-hidden="true"></div><main>{body}</main>'
        '</body></html>'
    )
