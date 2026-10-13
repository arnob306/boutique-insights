from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from src.reminder_messages import (
    DEFAULT_PATH,
    DEFAULT_TEMPLATES,
    Templates,
    festival_message,
    first_name,
    load_templates,
    nudge_message,
)

START = pd.Timestamp('2026-10-10')


# --- picking a first name ---------------------------------------------------------

@pytest.mark.parametrize('full, expected', [
    ('Priya Sharma', 'Priya'),
    ('  Priya   Sharma  ', 'Priya'),
    ('Priya', 'Priya'),
    ('Mrs Sen', 'Sen'),
    ('Mrs. Anita Sen', 'Anita'),
    ('Dr Rahul Das', 'Rahul'),
    ('', 'there'),
    ('   ', 'there'),
    ('Mrs', 'there'),
])
def test_the_greeting_uses_a_first_name_or_a_friendly_fallback(full, expected):
    assert first_name(full) == expected


def test_a_missing_name_gets_the_fallback():
    assert first_name(None) == 'there'


# --- the default wording ----------------------------------------------------------

def test_the_festival_message_names_the_person_festival_and_date():
    text = festival_message(DEFAULT_TEMPLATES, 'Priya Sharma', 'Durga Puja', START)
    assert text.startswith('Hi Priya,')
    assert 'Durga Puja' in text
    assert '10 Oct' in text


def test_the_nudge_message_names_the_person():
    assert nudge_message(DEFAULT_TEMPLATES, 'Priya Sharma').startswith('Hi Priya,')


def test_messages_never_quote_spend_or_buying_history():
    texts = [festival_message(DEFAULT_TEMPLATES, 'Priya Sharma', 'Durga Puja', START),
             nudge_message(DEFAULT_TEMPLATES, 'Priya Sharma')]
    for text in texts:
        assert '$' not in text
        assert 'last year' not in text.lower()
        assert 'spent' not in text.lower()


def test_a_name_with_braces_cannot_break_the_message():
    text = nudge_message(DEFAULT_TEMPLATES, '{festival} {oops}')
    assert text.startswith('Hi {festival},')


def test_the_message_makes_no_promise_about_price_or_stock():
    text = festival_message(DEFAULT_TEMPLATES, 'Priya', 'Durga Puja', START).lower()
    for promise in ('discount', 'off ', 'sale', 'new stock', 'in stock'):
        assert promise not in text


# --- the editable file --------------------------------------------------------------

def test_the_shipped_file_loads_and_has_both_messages():
    templates = load_templates(DEFAULT_PATH)
    assert templates.festival and templates.nudge
    festival_message(templates, 'Priya Sharma', 'Durga Puja', START)
    nudge_message(templates, 'Priya Sharma')


def test_a_missing_file_means_the_built_in_wording(tmp_path):
    assert load_templates(tmp_path / 'nope.yaml') == DEFAULT_TEMPLATES


def _write(tmp_path, body):
    path = tmp_path / 'messages.yaml'
    path.write_text(body, encoding='utf-8')
    return path


def test_her_wording_replaces_the_default_and_the_other_message_keeps_its_own(tmp_path):
    path = _write(tmp_path, "festival: 'Namaskar {first_name}! {festival} starts {date}.'\n")
    templates = load_templates(path)
    assert festival_message(templates, 'Priya Sharma', 'Durga Puja', START) == (
        'Namaskar Priya! Durga Puja starts 10 Oct.')
    assert templates.nudge == DEFAULT_TEMPLATES.nudge


def test_an_unknown_placeholder_is_rejected_with_its_name(tmp_path):
    path = _write(tmp_path, "nudge: 'Hi {first_name}, you spent {spend}.'\n")
    with pytest.raises(ValueError, match='spend'):
        load_templates(path)


def test_a_message_that_is_not_text_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='festival'):
        load_templates(_write(tmp_path, 'festival: 5\n'))


def test_a_file_that_is_not_a_set_of_messages_is_rejected(tmp_path):
    with pytest.raises(ValueError, match='messages'):
        load_templates(_write(tmp_path, '- just\n- a list\n'))


def test_broken_yaml_is_reported_in_plain_words(tmp_path):
    with pytest.raises(ValueError, match='could not be read'):
        load_templates(_write(tmp_path, 'festival: [unclosed\n'))


def test_an_empty_file_means_the_built_in_wording(tmp_path):
    assert load_templates(_write(tmp_path, '')) == DEFAULT_TEMPLATES


def test_templates_are_immutable():
    with pytest.raises(FrozenInstanceError):
        DEFAULT_TEMPLATES.festival = 'changed'
    assert isinstance(DEFAULT_TEMPLATES, Templates)
