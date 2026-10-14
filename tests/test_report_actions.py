import re
from dataclasses import replace

import pandas as pd
import pytest
from test_report import _build

from src.reports.actions import build_actions
from src.reports.playbook import PlaybookItem
from src.reports.stock_signals import OVERSTOCK_WEEKS, OverstockLine
from src.reports.weekly import (
    ReorderLine,
    build_weekly_report,
    render_html,
    render_text,
)
from src.validation.checks import validate_import


@pytest.fixture(scope='module')
def report(workbook_path):
    return _build(workbook_path)[1]


def _line(product, stock=1, per_week=1.0, lead=6, qty=4):
    return ReorderLine(product=product, order_qty=qty, stock_now=stock,
                       units_per_week=per_week, lead_weeks=lead,
                       confidence='good', note='')


def _festival(stage='order_soon', festival='Durga Puja'):
    start = pd.Timestamp('2026-10-10')
    return PlaybookItem(
        festival=festival, start=start, end=start + pd.Timedelta(days=11), stage=stage,
        order_by=start - pd.Timedelta(weeks=6), lead_weeks=6, lead_assumed=True,
        lifts=(), text=f'{festival} text.', cash=None)


def _quiet(report, **overrides):
    """The report with nothing that needs doing, plus any overrides."""
    base = replace(report, reorder=(), reorder_status='nothing', playbook=(),
                   reminder_count=None, festival_reminder_counts=(),
                   stock_age_days=None, recount=(), overstock=())
    return replace(base, **overrides)


def _busy(report):
    return _quiet(
        report,
        reorder=(_line('Short Cover', stock=1, per_week=1.0, lead=6),),
        reorder_status='orders', playbook=(_festival('order_soon'),),
        reminder_count=3)


def _section(text, title):
    """The lines of one titled section of the plain-text email."""
    lines = text.splitlines()
    start = lines.index(title)
    end = next((i for i in range(start + 1, len(lines)) if lines[i] == ''), len(lines))
    return lines[start + 1:end]


# --- where it sits --------------------------------------------------------------

def test_do_this_week_is_the_first_section_of_the_text_email(report):
    text = render_text(_busy(report))
    assert text.index('Do this week') < text.index('Reorder this week')
    assert text.index('Do this week') < text.index('This week\n')


def test_do_this_week_is_the_first_section_of_the_html_email(report):
    page = render_html(_busy(report))
    assert page.index('Do this week') < page.index('Reorder this week')


def test_the_not_trusted_warning_still_comes_first(report):
    text = render_text(replace(_busy(report), trusted=False))
    assert text.index('NOT TRUSTED') < text.index('Do this week')


# --- what it says ---------------------------------------------------------------

def test_a_quiet_week_says_nothing_is_urgent(report):
    lines = _section(render_text(_quiet(report)), 'Do this week')
    assert 'Nothing urgent this week.' in ' '.join(lines)


def test_without_a_stock_sheet_a_quiet_week_says_how_to_switch_reorders_on(report):
    quiet = _quiet(report, reorder_status='no_stock_sheet')
    assert 'Stock & Orders' in ' '.join(_section(render_text(quiet), 'Do this week'))


def test_each_action_starts_with_how_urgent_it_is_most_urgent_first(report):
    lines = _section(render_text(_busy(report)), 'Do this week')
    bullets = [line for line in lines if line.startswith('- ')]
    assert bullets[0].startswith('- Now: Order 4 Short Cover')
    assert any(b.startswith('- Soon: Durga Puja: order by') for b in bullets)
    assert bullets[-1].startswith('- FYI: 3 repeat customers are due a nudge')


def test_it_is_the_same_list_the_dashboard_shows(report):
    busy = _busy(report)
    text = render_text(busy)
    for action in build_actions(busy)[:3]:
        assert action.text in text


def test_only_the_three_most_urgent_are_shown_and_it_says_how_many_more(report):
    many = _quiet(
        report, reorder=tuple(_line(f'Saree {i}') for i in range(6)),
        reorder_status='orders')
    lines = _section(render_text(many), 'Do this week')
    assert len([line for line in lines if line.startswith('- ')]) == 3
    assert 'The 3 most urgent of 6' in ' '.join(lines)


def test_fewer_than_three_actions_do_not_claim_there_are_more(report):
    lines = _section(render_text(_quiet(report, reminder_count=2)), 'Do this week')
    assert 'most urgent of' not in ' '.join(lines)


# --- what it must not contain -----------------------------------------------------

def test_product_names_are_escaped_in_the_html_actions(report):
    nasty = _quiet(report, reorder=(_line('A&B <script>x</script>'),),
                   reorder_status='orders')
    page = render_html(nasty)
    assert '<script>' not in page
    assert 'A&amp;B' in page


def test_the_actions_give_counts_only_never_customer_details(report):
    busy = _busy(report)
    combined = render_text(busy) + render_html(busy)
    assert not set(re.findall(r'[0-9a-f]{16}', combined))
    assert '3 repeat customers are due a nudge' in combined


# --- recount: which stock numbers to refresh --------------------------------------

def _texts(report):
    return [a.text for a in build_actions(report)]


def test_recount_names_the_products_and_leads_the_soon_actions(report):
    busy = replace(_busy(report), recount=('Silk Saree', 'Gold Necklace'))
    actions = build_actions(busy)
    assert actions[0].urgency == 'now'
    assert actions[1].urgency == 'soon'
    assert actions[1].text.startswith('Recount these first: Silk Saree, Gold Necklace.')
    assert 'over 3 weeks old' in actions[1].text


def test_recount_shows_in_the_do_this_week_section_of_the_email(report):
    quiet = _quiet(report, reorder=(_line('Silk Saree'),), reorder_status='orders',
                   recount=('Silk Saree',))
    lines = _section(render_text(quiet), 'Do this week')
    assert any(line.startswith('- Soon: Recount these first: Silk Saree.')
               for line in lines)


def test_recount_product_names_are_escaped_in_the_html(report):
    nasty = _quiet(report, recount=('A&B <script>x</script>',))
    page = render_html(nasty)
    assert '<script>' not in page
    assert 'A&amp;B' in page


def test_an_old_sheet_with_nothing_to_order_says_when_it_was_counted(report):
    old = _quiet(report, stock_age_days=35)
    assert 'Stock was last counted 5 weeks ago. Recount a few products' in ' '.join(
        _texts(old))


def test_a_fresh_sheet_asks_for_no_recount(report):
    assert _texts(_quiet(report, stock_age_days=7)) == []


def test_no_stock_sheet_means_no_recount_action(report):
    assert _texts(_quiet(report, stock_age_days=None)) == []


def test_a_recount_list_replaces_the_generic_old_sheet_line(report):
    both = _quiet(report, recount=('Silk Saree',), stock_age_days=60)
    texts = _texts(both)
    assert len(texts) == 1
    assert texts[0].startswith('Recount these first')


# --- overstock: stock that will take a long time to sell --------------------------

def test_overstock_is_an_fyi_that_suggests_a_sale(report):
    over = _quiet(report, overstock=(OverstockLine('Old Kurta', 30, 40.0),))
    (action,) = build_actions(over)
    assert action.urgency == 'info'
    assert action.text == ('Old Kurta: about 40 weeks of stock (30 left) at the '
                           'current pace. Consider a sale to free up cash.')


def test_overstock_comes_after_the_urgent_actions(report):
    over = replace(_busy(report), overstock=(OverstockLine('Old Kurta', 30, 40.0),))
    urgencies = [a.urgency for a in build_actions(over)]
    assert urgencies == sorted(urgencies, key={'now': 0, 'soon': 1, 'info': 2}.get)
    assert urgencies[-1] == 'info'


def test_overstock_names_are_escaped_in_the_html(report):
    nasty = _quiet(report, overstock=(OverstockLine('A&B <script>x</script>', 30, 40.0),))
    page = render_html(nasty)
    assert '<script>' not in page
    assert 'A&amp;B' in page


# --- built from a real workbook ----------------------------------------------------

DAY = pd.Timedelta(days=1)


@pytest.fixture(scope='module')
def built(workbook_path):
    return _build(workbook_path)


def _rebuild(data, **stock_changes):
    data = replace(data, stock=data.stock.assign(**stock_changes))
    today = data.sales['date'].max() + DAY
    return build_weekly_report(data, validate_import(data, as_of=today), today=today)


def test_old_counts_of_products_to_order_become_a_recount_action(built):
    data, _ = built
    today = data.sales['date'].max() + DAY
    old = _rebuild(data, stock_on_hand=0, count_date=today - 60 * DAY)
    assert old.reorder
    assert old.recount
    assert set(old.recount) <= {line.product for line in old.reorder}
    assert any(t.startswith('Recount these first') for t in _texts(old))


def test_fresh_counts_and_plenty_of_stock_give_overstock_and_no_recount(built):
    data, _ = built
    today = data.sales['date'].max() + DAY
    plenty = _rebuild(data, stock_on_hand=2000, count_date=today - DAY)
    assert plenty.recount == ()
    assert plenty.stock_age_days == 1
    assert plenty.overstock
    assert all(line.weeks_of_cover > OVERSTOCK_WEEKS for line in plenty.overstock)
    assert any('Consider a sale' in t for t in _texts(plenty))


def test_without_a_stock_sheet_there_is_nothing_to_recount_or_clear(workbook_path):
    _, bare = _build(workbook_path, drop_stock=True)
    assert (bare.stock_age_days, bare.recount, bare.overstock) == (None, (), ())
