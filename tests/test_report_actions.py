import re
from dataclasses import replace

import pandas as pd
import pytest
from test_report import _build

from src.reports.actions import build_actions
from src.reports.playbook import PlaybookItem
from src.reports.weekly import ReorderLine, render_html, render_text


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
                   reminder_count=None, festival_reminder_counts=())
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
