import re
from dataclasses import replace

import pandas as pd
import pytest
from test_report import _build

from src.adapters.boutique_xlsx import load_workbook_data
from src.metrics.calendar import FestivalWindow
from src.metrics.cashplan import CashPlan
from src.metrics.trends import weekly_series
from src.reports.dashboard import MAX_ACTIONS, WEEKS_SHOWN, render_dashboard
from src.reports.playbook import Lift, PlaybookItem
from src.reports.weekly import ReorderLine, money

SALT = 'unit-test-salt-0123456789'


@pytest.fixture(scope='module')
def built(workbook_path):
    data, report = _build(workbook_path)
    series = weekly_series(data.sales, report.data_through, WEEKS_SHOWN)
    return data, report, series


@pytest.fixture(scope='module')
def page(built):
    _, report, series = built
    return render_dashboard(report, series)


def test_page_is_a_mobile_friendly_html_document(page):
    assert page.startswith('<!DOCTYPE html>')
    assert 'name="viewport"' in page
    assert 'viewBox=' in page


def test_page_is_self_contained(page):
    lowered = page.lower()
    assert '<script' not in lowered
    assert 'http://' not in lowered and 'https://' not in lowered
    assert '<link' not in lowered and '<img' not in lowered


def test_one_bar_is_drawn_for_each_week(built, page):
    _, _, series = built
    assert page.count('data-week=') == len(series)


def test_headline_figures_are_shown(built, page):
    _, report, _ = built
    assert 'Boutique dashboard' in page
    assert f'{report.data_through:%d %b %Y}' in page


def test_reorder_lines_appear_when_there_is_a_stock_sheet(built, page):
    _, report, _ = built
    assert report.reorder_status in ('orders', 'nothing')
    for line in report.reorder:
        assert line.product in page


def test_missing_stock_sheet_is_explained(workbook_path):
    data, report = _build(workbook_path, drop_stock=True)
    series = weekly_series(data.sales, report.data_through, WEEKS_SHOWN)
    assert 'Stock & Orders' in render_dashboard(report, series).replace('&amp;', '&')


def test_untrusted_data_shows_a_warning_banner(built):
    _, report, series = built
    page = render_dashboard(replace(report, trusted=False), series)
    assert 'NOT TRUSTED' in page


def test_trusted_data_has_no_warning_banner(page):
    assert 'NOT TRUSTED' not in page


def test_product_names_are_html_escaped(built):
    _, report, series = built
    top = replace(report.top_earners[0], product='<b>Evil</b> & Co')
    page = render_dashboard(replace(report, top_earners=(top,)), series)
    assert '<b>Evil</b>' not in page
    assert '&lt;b&gt;Evil&lt;/b&gt; &amp; Co' in page


def test_no_customer_identifiers_appear(workbook_path, page):
    data = load_workbook_data(workbook_path, salt=SALT)
    for customer_id in data.sales['customer_id'].dropna().unique()[:200]:
        assert customer_id not in page


def test_an_empty_series_says_there_is_nothing_to_chart(built):
    _, report, series = built
    page = render_dashboard(report, series.iloc[0:0])
    assert 'No sales to chart yet' in page
    assert 'data-week=' not in page


def test_a_loss_making_product_gets_the_loss_bar(built):
    _, report, series = built
    losing = replace(report.top_earners[0], profit=-50.0)
    page = render_dashboard(replace(report, top_earners=(losing,)), series)
    assert 'class="fill loss"' in page


def test_nothing_to_order_is_stated_plainly(built):
    _, report, series = built
    page = render_dashboard(
        replace(report, reorder=(), reorder_status='nothing'), series)
    assert 'Nothing needs ordering right now' in page


def test_the_headline_is_this_weeks_sales(built, page):
    _, report, _ = built
    assert 'Sold in the last 7 days' in page
    assert money(report.sales_this_week) in page


def test_only_the_newest_week_is_highlighted(built, page):
    _, _, series = built
    assert page.count('class="bar latest"') == 1
    newest = f'class="bar latest" data-week="{series.index[-1]:%Y-%m-%d}"'
    assert newest in page


def test_a_table_view_lists_every_week(built, page):
    _, _, series = built
    assert '<details' in page
    assert page.count('<tr>') == len(series) + 1  # header row plus one per week


def test_dark_mode_colours_are_defined(page):
    assert 'prefers-color-scheme:dark' in page


def test_reorder_rows_compare_stock_cover_with_shipment_time(built):
    _, report, series = built
    line = ReorderLine(
        product='Test Saree', order_qty=8, stock_now=4, units_per_week=0.5,
        lead_weeks=7, confidence='good', note='')
    page = render_dashboard(
        replace(report, reorder=(line,), reorder_status='orders'), series)
    assert 'data-reorder="Test Saree"' in page
    assert 'data-cover-weeks="8.0"' in page
    assert 'data-lead-weeks="7"' in page


def test_stock_that_will_not_sell_has_no_cover_figure(built):
    _, report, series = built
    line = ReorderLine(
        product='Slow Saree', order_qty=2, stock_now=4, units_per_week=0.0,
        lead_weeks=7, confidence='low', note='no recent sales')
    page = render_dashboard(
        replace(report, reorder=(line,), reorder_status='orders'), series)
    assert 'data-reorder="Slow Saree"' in page
    assert 'data-cover-weeks' not in page


def test_one_week_is_not_pluralised(built):
    _, report, series = built
    line = ReorderLine(
        product='Fast Saree', order_qty=3, stock_now=1, units_per_week=1.0,
        lead_weeks=1, confidence='good', note='')
    page = render_dashboard(
        replace(report, reorder=(line,), reorder_status='orders'), series)
    assert 'lasts 1 week;' in page
    assert 'takes 1 week.' in page
    assert '1 weeks' not in page


def _with_festival(workbook_path, days_ahead):
    probe = load_workbook_data(workbook_path, salt=SALT)
    start = probe.sales['date'].max() + days_ahead * pd.Timedelta(days=1)
    calendar = [FestivalWindow('Durga Puja', start, start + pd.Timedelta(days=10))]
    data, report = _build(workbook_path, calendar=calendar)
    return render_dashboard(report, weekly_series(
        data.sales, report.data_through, WEEKS_SHOWN))


def test_the_festival_playbook_is_on_the_dashboard(workbook_path):
    page = _with_festival(workbook_path, days_ahead=40)
    assert 'Festival playbook' in page
    assert 'Durga Puja' in page
    assert 'not financial advice' in page


def test_the_dashboard_shows_the_reminder_count_only_when_there_is_one(built):
    _, report, series = built
    assert 'Customers to nudge' not in render_dashboard(report, series)
    page = render_dashboard(replace(report, reminder_count=7), series)
    assert 'Customers to nudge' in page
    assert '7 repeat customers are due a nudge' in page


def test_the_dashboard_shows_festival_reminder_counts(built):
    _, report, series = built
    page = render_dashboard(replace(
        report, reminder_count=2, festival_reminder_counts=(('Durga Puja', 9),)), series)
    assert '9 customers bought at Durga Puja last year' in page


def test_the_dashboard_has_a_mix_and_channels_section(built, page):
    _, report, series = built
    assert 'Mix and channels' in page
    assert report.mix_notes[0].split(' ')[0] in page
    assert 'Mix and channels' not in render_dashboard(
        replace(report, mix_notes=()), series)


def test_the_dashboard_has_no_playbook_section_without_a_calendar(page):
    assert 'Festival playbook' not in page


# --- the redesign: answer "what do I do?" first --------------------------------

def _reorder_line(product, stock=1, per_week=1.0, lead=6, qty=4):
    return ReorderLine(product=product, order_qty=qty, stock_now=stock,
                       units_per_week=per_week, lead_weeks=lead,
                       confidence='good', note='')


def _festival_item(stage='order_now', festival='Durga Puja', cash='none', lifts=()):
    start = pd.Timestamp('2026-10-10')
    return PlaybookItem(
        festival=festival, start=start, end=start + pd.Timedelta(days=11), stage=stage,
        order_by=start - pd.Timedelta(weeks=6), lead_weeks=6, lead_assumed=True,
        lifts=tuple(lifts), text=f'{festival} text.', cash=None if cash == 'none' else cash)


def _cash(**overrides):
    fields = dict(festival='Durga Puja', expected=1000.0, low=600.0, high=2000.0,
                  lift_windows=4, backtest_windows=12, budget_low=300.0,
                  budget_expected=500.0)
    return CashPlan(**{**fields, **overrides})


def _quiet(report, **overrides):
    """The report with nothing that needs doing, plus any overrides."""
    base = replace(report, reorder=(), reorder_status='nothing', playbook=(),
                   reminder_count=None, festival_reminder_counts=())
    return replace(base, **overrides)


def _actions_block(page):
    start = page.index('id="actions"')
    return page[start:page.index('</section>', start)]


def test_the_first_section_says_what_to_do_this_week(page):
    assert 'id="actions"' in page
    assert 'Do this week' in page
    assert page.index('id="actions"') < page.index('id="sales"')


def test_a_quiet_week_says_nothing_is_urgent(built):
    _, report, series = built
    page = render_dashboard(_quiet(report), series)
    assert 'Nothing urgent this week' in _actions_block(page)


def test_actions_are_ordered_most_urgent_first(built):
    _, report, series = built
    page = render_dashboard(_quiet(
        report,
        reorder=(_reorder_line('Long Cover', stock=40, per_week=1.0, lead=3),
                 _reorder_line('Short Cover', stock=1, per_week=1.0, lead=6)),
        reorder_status='orders',
        playbook=(_festival_item('order_soon'),),
        reminder_count=3), series)
    urgencies = re.findall(r'data-urgency="(\w+)"', _actions_block(page))
    assert urgencies == ['now', 'soon', 'soon', 'info']
    assert _actions_block(page).index('Short Cover') < _actions_block(page).index('Long Cover')


def test_a_festival_past_its_order_by_date_is_an_order_now_action(built):
    _, report, series = built
    page = render_dashboard(_quiet(report, playbook=(_festival_item('order_now'),)), series)
    block = _actions_block(page)
    assert 'Durga Puja' in block and 'order now' in block.lower()
    assert 'data-urgency="now"' in block


def test_every_action_pill_says_its_urgency_in_words(built):
    _, report, series = built
    page = render_dashboard(_quiet(
        report, reorder=(_reorder_line('Short Cover'),), reorder_status='orders',
        playbook=(_festival_item('order_soon'),), reminder_count=2), series)
    pills = re.findall(r'class="pill \w+">([^<]+)</span>', _actions_block(page))
    assert pills == ['Now', 'Soon', 'FYI']


def test_the_action_list_is_capped_with_a_pointer_to_the_rest(built):
    _, report, series = built
    lines = tuple(_reorder_line(f'Saree {i}') for i in range(MAX_ACTIONS + 3))
    page = render_dashboard(_quiet(report, reorder=lines, reorder_status='orders'), series)
    block = _actions_block(page)
    assert block.count('data-urgency=') == MAX_ACTIONS
    assert '3 more in the sections below' in block


def test_stock_that_runs_out_just_before_the_shipment_does_not_read_as_a_tie(built):
    _, report, series = built
    line = _reorder_line('Gold Bangles', stock=19, per_week=5.0, lead=4)  # 3.8 weeks
    page = render_dashboard(_quiet(report, reorder=(line,), reorder_status='orders'), series)
    block = _actions_block(page)
    assert 'runs out in 3.8 weeks' in block
    assert 'runs out in 4 weeks' not in block


# --- status tiles ------------------------------------------------------------------

@pytest.mark.parametrize('sales,usual,trend,glyph', [
    (1200.0, 1000.0, 'up', '▲'),
    (800.0, 1000.0, 'down', '▼'),
    (1000.0, 1000.0, 'flat', '='),
])
def test_the_status_tile_compares_with_a_usual_week(built, sales, usual, trend, glyph):
    _, report, series = built
    page = render_dashboard(
        replace(report, sales_this_week=sales, usual_week_sales=usual), series)
    assert f'data-trend="{trend}"' in page
    assert glyph in page


def test_without_a_usual_week_there_is_no_trend_tile(built):
    _, report, series = built
    page = render_dashboard(replace(report, usual_week_sales=None), series)
    assert 'data-trend=' not in page


def test_profit_and_margin_have_their_own_tile(built):
    _, report, series = built
    page = render_dashboard(
        replace(report, profit_this_week=321.0, overall_margin=0.47), series)
    assert money(321.0) in page and '47%' in page


# --- the chart gets a reference and a comparison ----------------------------------

def _series(values):
    index = pd.date_range('2026-06-01', periods=len(values), freq='7D')
    return pd.DataFrame({'sales': values, 'profit': [v / 2 for v in values],
                         'units': [1] * len(values)}, index=index)


def test_the_chart_marks_a_usual_week(built):
    _, report, series = built
    assert 'data-usual-week=' in render_dashboard(
        replace(report, usual_week_sales=500.0), series)


def test_there_is_no_usual_line_without_a_usual_week(built):
    _, report, series = built
    assert 'data-usual-week=' not in render_dashboard(
        replace(report, usual_week_sales=None), series)


def test_the_last_four_weeks_are_compared_with_the_four_before(built):
    _, report, _ = built
    page = render_dashboard(report, _series([100.0] * 4 + [150.0] * 4))
    assert f'Last 4 weeks: {money(600.0)}, up 50% on the 4 weeks before.' in page


def test_a_fall_is_called_a_fall(built):
    _, report, _ = built
    page = render_dashboard(report, _series([200.0] * 4 + [100.0] * 4))
    assert 'down 50% on the 4 weeks before' in page


def test_short_history_gets_no_four_week_comparison(built):
    _, report, _ = built
    assert 'Last 4 weeks' not in render_dashboard(report, _series([100.0] * 7))


def test_no_comparison_when_the_earlier_weeks_sold_nothing(built):
    _, report, _ = built
    assert 'Last 4 weeks' not in render_dashboard(report, _series([0.0] * 4 + [50.0] * 4))


# --- festival cards ----------------------------------------------------------------

def test_a_festival_card_draws_the_expected_sales_and_range(built):
    _, report, series = built
    page = render_dashboard(replace(
        report, playbook=(_festival_item(cash=_cash()),)), series)
    assert 'data-festival="Durga Puja"' in page
    assert 'data-expected="1000"' in page
    assert 'data-range-low="600"' in page and 'data-range-high="2000"' in page
    assert f'{money(600.0)} to {money(2000.0)}' in page
    assert 'stock budget' in page.lower()


def test_a_card_without_a_cash_plan_has_no_range_bar(built):
    _, report, series = built
    page = render_dashboard(replace(report, playbook=(_festival_item(),)), series)
    assert 'data-festival="Durga Puja"' in page
    assert 'data-range-low=' not in page and 'data-expected=' not in page


def test_a_card_without_a_range_says_there_is_not_enough_history(built):
    _, report, series = built
    page = render_dashboard(replace(report, playbook=(
        _festival_item(cash=_cash(low=None, high=None, budget_low=None)),)), series)
    assert 'data-expected="1000"' in page
    assert 'data-range-low=' not in page
    assert 'not enough history for a range' in page


def test_a_card_says_its_stage_in_words_and_shows_the_lifts(built):
    _, report, series = built
    page = render_dashboard(replace(report, playbook=(_festival_item(
        'order_soon', lifts=[Lift('Saree', 3.0, 5, 'solid')]),)), series)
    assert 'Order soon' in page
    assert 'Saree' in page and '3.0x' in page and 'solid' in page


# --- navigation and the data footer ------------------------------------------------

def test_every_jump_link_points_at_a_section_that_exists(built):
    _, report, series = built
    page = render_dashboard(replace(
        report, playbook=(_festival_item(cash=_cash()),), reminder_count=2), series)
    targets = re.findall(r'href="#([\w-]+)"', page)
    assert len(targets) >= 4
    for target in targets:
        assert f'id="{target}"' in page


def test_data_notes_are_tucked_into_a_labelled_summary(built):
    _, report, series = built
    page = render_dashboard(replace(report, data_notes=('One odd row.', 'Two odd rows.')), series)
    assert 'Data checks (2)' in page and 'One odd row.' in page
    assert 'Data checks' not in render_dashboard(replace(report, data_notes=()), series)
