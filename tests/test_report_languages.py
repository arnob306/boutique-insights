import re
from dataclasses import replace

import pandas as pd
import pytest
from test_report import _build
from test_report_actions import _busy, _line, _quiet, _section

from src.metrics.trends import weekly_series
from src.reports.actions import action_line, build_actions
from src.reports.dashboard import WEEKS_SHOWN, render_dashboard
from src.reports.delivery import EmailSettings, build_message
from src.reports.stock_signals import OverstockLine
from src.reports.weekly import (
    build_weekly_report,
    render_html,
    render_text,
)
from src.strings import DEFAULT_PATH, ENGLISH, Voice, load_languages
from src.validation.checks import validate_import

VOICES = load_languages(DEFAULT_PATH, language='both').voices
BN = VOICES[1]
BENGALI = re.compile('[ঀ-৿]')
HEADING = f"Do this week / {BN.say('title.do_this_week')}"


@pytest.fixture(scope='module')
def built(workbook_path):
    data, report = _build(workbook_path)
    series = weekly_series(data.sales, report.data_through, WEEKS_SHOWN)
    return data, report, series


@pytest.fixture(scope='module')
def report(built):
    return built[1]


def _both(report):
    return replace(_busy(report), voices=VOICES)


# --- the report carries its languages ----------------------------------------------

def test_a_report_is_english_only_unless_told_otherwise(report):
    assert report.voices == (ENGLISH,)
    assert all(a.also == () for a in build_actions(_busy(report)))


def test_the_builder_passes_the_languages_to_the_report(built):
    data = replace(built[0], stock=None)
    today = data.sales['date'].max() + pd.Timedelta(days=1)
    made = build_weekly_report(data, validate_import(data, as_of=today),
                               today=today, voices=VOICES)
    assert made.voices == VOICES


# --- the actions in two languages ---------------------------------------------------

def test_each_action_also_comes_in_bengali_with_its_own_urgency_word(report):
    for action in build_actions(_both(report)):
        (also,) = action.also
        assert BENGALI.search(also.text)
        assert also.urgency == action.urgency
        assert also.word == BN.urgency(action.urgency)


def test_the_bengali_reorder_line_says_the_same_thing_as_the_english_one(report):
    first = build_actions(_both(report))[0]
    assert first.text.startswith('Order 4 Short Cover: stock runs out in 1 week')
    assert first.also[0].text == BN.say(
        'reorder.runs_out', qty=4, product='Short Cover',
        left=BN.weeks(1.0), lead=BN.weeks(6))


def test_the_bengali_festival_and_reminder_lines_match_the_english_ones(report):
    actions = build_actions(_both(report))
    festival = next(a for a in actions if a.text.startswith('Durga Puja'))
    assert festival.also[0].text == BN.say(
        'festival.order_soon', festival='Durga Puja', order_by='29 Aug')
    reminder = actions[-1]
    assert reminder.also[0].text == BN.say('reminder.many', count=3)


def test_the_bengali_recount_and_overstock_lines(report):
    rep = _quiet(report, voices=VOICES, recount=('Silk Saree',),
                 overstock=(OverstockLine('Old Kurta', 30, 40.0),))
    recount, overstock = build_actions(rep)
    assert recount.also[0].text == BN.say(
        'recount.products', products='Silk Saree', old=BN.weeks(3))
    assert overstock.also[0].text == BN.say(
        'overstock', product='Old Kurta', cover=BN.weeks(40), units=30)


def test_a_bengali_only_report_has_bengali_text_and_words_and_no_second_line(report):
    rep = replace(_busy(report), voices=(BN,))
    actions = build_actions(rep)
    assert all(BENGALI.search(a.text) and a.also == () for a in actions)
    assert action_line(actions[0]).startswith(BN.urgency('now') + ': ')


def test_a_missing_translation_does_not_repeat_the_english_line(report):
    untranslated = Voice('bn', ENGLISH.table)
    rep = replace(_busy(report), voices=(ENGLISH, untranslated))
    assert all(a.also == () for a in build_actions(rep))


# --- the email ----------------------------------------------------------------------

def test_the_text_email_shows_the_english_line_then_its_bengali_line(report):
    lines = _section(render_text(_both(report)), HEADING)
    assert lines[0].startswith('- Now: Order 4 Short Cover')
    assert lines[1].startswith(f"  {BN.urgency('now')}: ")
    assert BENGALI.search(lines[1])


def test_the_quiet_week_note_is_in_both_languages(report):
    lines = _section(render_text(replace(_quiet(report), voices=VOICES)), HEADING)
    assert 'Nothing urgent this week.' in ' '.join(lines)
    assert BN.say('quiet') in ' '.join(lines)


def test_the_more_urgent_note_is_in_both_languages(report):
    many = replace(
        _quiet(report, reorder=tuple(_line(f'Saree {i}') for i in range(6)),
               reorder_status='orders'), voices=VOICES)
    joined = ' '.join(_section(render_text(many), HEADING))
    assert 'The 3 most urgent of 6' in joined
    assert BN.say('more', shown=3, total=6) in joined


def test_the_no_stock_sheet_hint_is_in_both_languages(report):
    bare = replace(_quiet(report, reorder_status='no_stock_sheet'), voices=VOICES)
    joined = ' '.join(_section(render_text(bare), HEADING))
    assert 'Stock & Orders' in joined
    assert BN.say('quiet.no_stock_sheet').strip() in joined


def test_the_html_email_puts_the_bengali_on_its_own_line(report):
    page = render_html(_both(report))
    assert BENGALI.search(page)
    assert '<br>' in page
    assert HEADING in page


def test_html_email_escapes_names_in_both_languages(report):
    nasty = replace(
        _quiet(report, reorder=(_line('A&B <script>x</script>'),),
               reorder_status='orders'), voices=VOICES)
    page = render_html(nasty)
    assert '<script>' not in page
    assert page.count('A&amp;B') >= 2


def test_an_english_report_is_unchanged_by_the_language_support(report):
    text = render_text(_busy(report))
    assert not BENGALI.search(text)
    assert 'Do this week\n' in text
    assert '<br>' not in render_html(_busy(report))


def test_the_email_is_sent_as_utf_8(report):
    rep = _both(report)
    settings = EmailSettings('smtp.example.com', 587, 'me@example.com', 'pw',
                             'you@example.com')
    message = build_message('Weekly', render_text(rep), render_html(rep), settings)
    plain = message.get_body(('plain',))
    assert plain.get_content_charset() == 'utf-8'
    assert BENGALI.search(plain.get_content())


# --- the dashboard -------------------------------------------------------------------

def test_the_dashboard_shows_both_languages_in_one_action_row(built):
    _, report, series = built
    page = render_dashboard(_both(report), series)
    start = page.index('id="actions"')
    block = page[start:page.index('</section>', start)]
    assert 'lang="bn"' in block
    assert BN.say('reorder.runs_out', qty=4, product='Short Cover',
                  left=BN.weeks(1.0), lead=BN.weeks(6)) in block
    assert HEADING in page


def test_a_bengali_only_dashboard_has_a_bengali_pill(built):
    _, report, series = built
    page = render_dashboard(replace(_busy(report), voices=(BN,)), series)
    assert f'<span class="pill now">{BN.urgency("now")}</span>' in page


def test_the_english_dashboard_is_unchanged(built):
    _, report, series = built
    page = render_dashboard(_busy(report), series)
    assert 'lang="bn"' not in page
    assert '<span class="pill now">Now</span>' in page
