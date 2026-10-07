import re
from dataclasses import replace

import pandas as pd
import pytest

from src.adapters.boutique_xlsx import load_workbook_data
from src.metrics.calendar import FestivalWindow
from src.metrics.products import product_performance
from src.reports.weekly import (
    MAX_REORDER_LINES,
    ReorderLine,
    build_weekly_report,
    render_html,
    render_text,
    subject,
)
from src.validation.checks import validate_import

SALT = 'unit-test-salt-0123456789'
DAY = pd.Timedelta(days=1)


def _build(path, calendar=None, drop_stock=False, days_after=1):
    data = load_workbook_data(path, salt=SALT)
    if drop_stock:
        data = replace(data, stock=None)
    today = data.sales['date'].max() + days_after * DAY
    validation = validate_import(data, as_of=today)
    report = build_weekly_report(data, validation, today=today, calendar=calendar)
    return data, report


@pytest.fixture(scope='module')
def built(workbook_path):
    return _build(workbook_path)


def _window_sales(data, report, shift_days=0):
    start = report.week_start - shift_days * DAY
    end = report.week_end - shift_days * DAY
    sales = data.sales
    return sales[(sales['date'] >= start) & (sales['date'] <= end)]


def test_the_week_is_the_seven_days_ending_at_the_last_sale(built):
    data, report = built
    assert report.week_end == data.sales['date'].max()
    assert report.week_start == report.week_end - 6 * DAY
    assert report.data_through == report.week_end


def test_this_weeks_sales_are_net_of_refunds(built):
    data, report = built
    window = _window_sales(data, report)
    assert report.sales_this_week == pytest.approx(window['line_total'].sum())
    assert report.sales_count_this_week == (window['quantity'] > 0).sum()


def test_this_week_is_compared_with_a_usual_week_and_last_year(built):
    data, report = built
    usual = sum(
        _window_sales(data, report, shift_days=7 * k)['line_total'].sum()
        for k in range(1, 9)
    ) / 8
    last_year = _window_sales(data, report, shift_days=364)['line_total'].sum()
    assert report.usual_week_sales == pytest.approx(usual)
    assert report.sales_last_year == pytest.approx(last_year)


def test_usual_week_needs_eight_weeks_of_history(make_workbook):
    path = make_workbook(start='2026-09-01')
    _, report = _build(path, days_after=0)
    assert report.usual_week_sales is None


def test_reorder_lists_only_items_that_need_ordering(built):
    _, report = built
    assert report.reorder_status in {'orders', 'nothing'}
    assert len(report.reorder) <= MAX_REORDER_LINES
    assert all(line.order_qty > 0 for line in report.reorder)
    quantities = [line.order_qty for line in report.reorder]
    assert quantities == sorted(quantities, reverse=True)


def test_without_a_stock_sheet_the_report_says_how_to_enable_reorders(workbook_path):
    _, report = _build(workbook_path, drop_stock=True)
    assert report.reorder_status == 'no_stock_sheet'
    assert report.reorder == ()
    assert 'Stock & Orders' in render_text(report)


def test_top_earners_are_ranked_by_profit_over_the_last_year(built):
    data, report = built
    start = report.data_through - pd.Timedelta(days=364)
    expected = list(product_performance(data.sales, start=start).index[:5])
    assert [line.product for line in report.top_earners] == expected


def test_small_earners_are_the_lowest_profit_products(built):
    data, report = built
    start = report.data_through - pd.Timedelta(days=364)
    perf = product_performance(data.sales, start=start)
    perf = perf[perf['units_sold'] > 0]
    expected = set(perf.sort_values('profit').index[:3])
    assert {line.product for line in report.small_earners} == expected


def test_an_upcoming_festival_is_announced_with_its_effect(workbook_path):
    probe = load_workbook_data(workbook_path, salt=SALT)
    start = probe.sales['date'].max() + 11 * DAY
    calendar = [FestivalWindow('Durga Puja', start, start + 10 * DAY)]
    _, report = _build(workbook_path, calendar=calendar)
    item = next(i for i in report.playbook if i.festival == 'Durga Puja')
    assert item.stage == 'preview'
    assert 'Durga Puja' in render_text(report)


def test_no_festival_means_no_playbook(built):
    assert built[1].playbook == ()
    assert 'Festival playbook' not in render_text(built[1])


def test_playbook_uses_the_default_lead_time_without_a_stock_sheet(workbook_path):
    probe = load_workbook_data(workbook_path, salt=SALT)
    start = probe.sales['date'].max() + 70 * DAY
    calendar = [FestivalWindow('Durga Puja', start, start + 10 * DAY)]
    _, report = _build(workbook_path, calendar=calendar, drop_stock=True)
    assert report.playbook[0].lead_assumed
    assert 'assum' in render_text(report).lower()


def test_playbook_uses_the_stock_sheets_lead_time_when_there_is_one(workbook_path):
    probe = load_workbook_data(workbook_path, salt=SALT)
    start = probe.sales['date'].max() + 70 * DAY
    calendar = [FestivalWindow('Durga Puja', start, start + 10 * DAY)]
    _, report = _build(workbook_path, calendar=calendar)
    assert not report.playbook[0].lead_assumed


def test_no_reminder_count_means_no_reminder_section(built):
    assert built[1].reminder_count is None
    assert 'Customers to nudge' not in render_text(built[1])


@pytest.mark.parametrize('count, wording', [
    (0, 'Nobody is due a nudge'),
    (1, '1 repeat customer is due a nudge'),
    (12, '12 repeat customers are due a nudge'),
])
def test_the_reminder_section_gives_only_a_count(built, count, wording):
    report = replace(built[1], reminder_count=count)
    for page in (render_text(report), render_html(report)):
        assert 'Customers to nudge' in page
        assert wording in page
    if count:
        assert 'saved on your computer' in render_text(report)


def test_festival_reminder_counts_appear_as_numbers_only(built):
    report = replace(built[1], reminder_count=3,
                     festival_reminder_counts=(('Durga Puja', 9), ('Diwali / Kali Puja', 1)))
    for page in (render_text(report), render_html(report)):
        assert '9 customers bought at Durga Puja last year' in page
        assert '1 customer bought at Diwali / Kali Puja last year' in page


def test_no_festival_counts_adds_no_festival_lines(built):
    report = replace(built[1], reminder_count=3)
    assert 'bought at' not in render_text(report)


def _report_with_festival(workbook_path, days_ahead=70):
    probe = load_workbook_data(workbook_path, salt=SALT)
    start = probe.sales['date'].max() + days_ahead * DAY
    calendar = [FestivalWindow('Durga Puja', start, start + 10 * DAY)]
    return _build(workbook_path, calendar=calendar)[1]


def test_the_playbook_carries_a_cash_plan_with_a_range(workbook_path):
    report = _report_with_festival(workbook_path)
    cash = report.playbook[0].cash
    assert cash is not None and cash.low is not None
    assert cash.low <= cash.expected <= cash.high
    text = render_text(report)
    assert 'Expected sales in the window' in text and 'stock budget' in text.lower()


def test_the_stock_budget_uses_her_overall_cost_share(workbook_path):
    report = _report_with_festival(workbook_path)
    cash = report.playbook[0].cash
    assert cash.budget_expected == pytest.approx(
        cash.expected * (1 - report.overall_margin))


def test_no_cash_plan_once_it_is_too_late_to_order(workbook_path):
    report = _report_with_festival(workbook_path, days_ahead=10)
    assert report.playbook[0].cash is None
    assert 'Expected sales' not in render_text(report)


def test_playbook_is_in_the_text_and_html_email_with_its_footer(workbook_path):
    probe = load_workbook_data(workbook_path, salt=SALT)
    start = probe.sales['date'].max() + 40 * DAY
    calendar = [FestivalWindow('Durga Puja', start, start + 10 * DAY)]
    _, report = _build(workbook_path, calendar=calendar)
    for page in (render_text(report), render_html(report)):
        assert 'Festival playbook' in page
        assert 'Durga Puja' in page
        assert 'not financial advice' in page


def test_critical_problems_make_the_report_untrusted(make_workbook):
    path = make_workbook(problems={'duplicate_id'})
    _, report = _build(path, days_after=0)
    assert not report.trusted
    assert 'not trusted' in render_text(report).lower()
    assert 'not trusted' in render_html(report).lower()


def test_clean_data_is_trusted(built):
    assert built[1].trusted


def test_data_warnings_are_listed_in_plain_english(make_workbook):
    path = make_workbook(problems={'unknown_product'})
    _, report = _build(path, days_after=0)
    assert any('Mystery Item' in note for note in report.data_notes)


def test_text_report_has_the_two_answers(built):
    text = render_text(built[1])
    assert 'Reorder' in text
    assert 'making money' in text.lower()
    assert 'data through' in text.lower()
    assert '$' in text


def test_stock_on_order_is_shown_in_the_reorder_line(workbook_path):
    data = load_workbook_data(workbook_path, salt=SALT)
    stock = data.stock.assign(on_order=2, stock_on_hand=0)
    data = replace(data, stock=stock)
    today = data.sales['date'].max() + DAY
    report = build_weekly_report(
        data, validate_import(data, as_of=today), today=today)
    assert report.reorder
    assert all(line.on_order == 2 for line in report.reorder)
    assert '2 on order' in render_text(report)


def test_html_escapes_product_names(built):
    _, report = built
    nasty = ReorderLine('A&B <script>x</script>', 5, 1, 1.0, 4, 'good', '')
    html = render_html(replace(report, reorder=(nasty,), reorder_status='orders'))
    assert '&amp;' in html
    assert '<script>' not in html


def test_html_is_self_contained(built):
    html = render_html(built[1])
    for forbidden in ('http://', 'https://', '<img', '<link', '<script'):
        assert forbidden not in html


def test_outputs_never_contain_customer_ids(built):
    data, report = built
    ids = set(data.sales['customer_id'].dropna())
    combined = render_text(report) + render_html(report)
    assert not ids & set(re.findall(r'[0-9a-f]{16}', combined))


def test_subject_names_the_week(built):
    line = subject(built[1])
    assert 'Boutique weekly summary' in line
    assert f'{built[1].week_end:%d %b %Y}' in line
