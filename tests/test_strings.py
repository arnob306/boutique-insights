import re
import string

import pytest
import yaml

from src.strings import DEFAULT_PATH, ENGLISH, load_languages

BENGALI = re.compile('[ঀ-৿]')


def _write(tmp_path, text):
    path = tmp_path / 'strings.yaml'
    path.write_text(text, encoding='utf-8')
    return path


def _fields(text):
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


# --- the English voice -------------------------------------------------------------

def test_a_message_fills_in_its_placeholders():
    assert ENGLISH.say('festival.on_now', festival='Durga Puja') == (
        'Durga Puja is on. Keep an eye on stock.')


def test_weeks_are_singular_for_one_and_plural_otherwise():
    assert ENGLISH.weeks(1) == '1 week'
    assert ENGLISH.weeks(3) == '3 weeks'
    assert ENGLISH.weeks(0.4) == '0 weeks'


def test_exact_weeks_keep_one_decimal_and_read_as_plural():
    assert ENGLISH.weeks(3.8, exact=True) == '3.8 weeks'
    assert ENGLISH.weeks(1.0, exact=True) == '1.0 weeks'


def test_the_urgency_words_are_in_the_voice():
    assert [ENGLISH.urgency(u) for u in ('now', 'soon', 'info')] == ['Now', 'Soon', 'FYI']


# --- reading her wording -----------------------------------------------------------

def test_without_a_strings_file_it_is_english_only(tmp_path):
    found = load_languages(tmp_path / 'missing.yaml')
    assert found.voices == (ENGLISH,)
    assert found.notices == ()


def test_an_empty_file_is_english_only(tmp_path):
    assert load_languages(_write(tmp_path, '')).voices == (ENGLISH,)


def test_both_gives_english_then_bengali_and_missing_keys_fall_back(tmp_path):
    path = _write(tmp_path, 'language: both\nbn:\n  urgency.now: "এখনই"\n')
    english, bengali = load_languages(path).voices
    assert english is ENGLISH
    assert bengali.code == 'bn'
    assert bengali.urgency('now') == 'এখনই'
    assert bengali.urgency('soon') == 'Soon'


def test_bn_alone_gives_only_the_bengali_voice(tmp_path):
    path = _write(tmp_path, 'language: bn\nbn:\n  urgency.now: "এখনই"\n')
    (only,) = load_languages(path).voices
    assert only.code == 'bn'


def test_a_requested_language_beats_the_file(tmp_path):
    path = _write(tmp_path, 'language: en\nbn:\n  urgency.now: "এখনই"\n')
    assert [v.code for v in load_languages(path, language='both').voices] == ['en', 'bn']


def test_a_language_that_is_not_offered_is_explained(tmp_path):
    path = _write(tmp_path, 'language: french\n')
    with pytest.raises(ValueError, match='en, bn or both'):
        load_languages(path)


def test_a_requested_language_that_is_not_offered_is_explained(tmp_path):
    with pytest.raises(ValueError, match='en, bn or both'):
        load_languages(tmp_path / 'missing.yaml', language='french')


def test_asking_for_bengali_with_none_written_says_so_and_stays_english(tmp_path):
    found = load_languages(_write(tmp_path, 'language: both\n'))
    assert found.voices == (ENGLISH,)
    assert 'No Bengali wording' in ' '.join(found.notices)


def test_an_unknown_key_is_ignored_with_a_notice(tmp_path):
    path = _write(tmp_path, 'bn:\n  urgency.now: "এখনই"\n  nonsense.key: "x"\n')
    found = load_languages(path, language='both')
    assert len(found.voices) == 2
    assert any('nonsense.key' in n for n in found.notices)


def test_a_wrong_placeholder_falls_back_for_that_key_only(tmp_path):
    path = _write(tmp_path, 'bn:\n  urgency.now: "এখনই"\n'
                            '  festival.on_now: "{wrong} চলছে"\n')
    found = load_languages(path, language='both')
    bengali = found.voices[1]
    assert bengali.urgency('now') == 'এখনই'
    assert bengali.say('festival.on_now', festival='Durga Puja') == ENGLISH.say(
        'festival.on_now', festival='Durga Puja')
    assert any('festival.on_now' in n and '{festival}' in n for n in found.notices)


def test_a_stray_curly_bracket_falls_back_with_a_notice(tmp_path):
    path = _write(tmp_path, 'bn:\n  urgency.now: "এখনই"\n  festival.on_now: "{festival চলছে"\n')
    found = load_languages(path, language='both')
    assert any('festival.on_now' in n for n in found.notices)
    assert found.voices[1].say('festival.on_now', festival='X') == ENGLISH.say(
        'festival.on_now', festival='X')


def test_a_value_that_is_not_text_falls_back_with_a_notice(tmp_path):
    path = _write(tmp_path, 'bn:\n  urgency.now: "এখনই"\n  urgency.soon: 5\n')
    found = load_languages(path, language='both')
    assert found.voices[1].urgency('soon') == 'Soon'
    assert any('urgency.soon' in n for n in found.notices)


def test_a_bn_section_that_is_not_a_list_of_messages_is_explained(tmp_path):
    with pytest.raises(ValueError, match='bn'):
        load_languages(_write(tmp_path, 'bn: just some words\n'))


def test_a_file_that_is_not_a_set_of_settings_is_explained(tmp_path):
    with pytest.raises(ValueError, match='strings.yaml'):
        load_languages(_write(tmp_path, '- a\n- b\n'))


def test_a_broken_file_is_explained_in_plain_words(tmp_path):
    with pytest.raises(ValueError, match='could not be read'):
        load_languages(_write(tmp_path, 'language: [unclosed\n'))


# --- the Bengali that ships with the project ----------------------------------------

@pytest.fixture(scope='module')
def shipped():
    return yaml.safe_load(DEFAULT_PATH.read_text(encoding='utf-8'))


def test_every_english_message_has_a_bengali_one(shipped):
    assert set(shipped['bn']) == set(ENGLISH.table)


def test_every_bengali_message_is_written_in_bengali_script(shipped):
    plain = [key for key, text in shipped['bn'].items() if not BENGALI.search(text)]
    assert plain == []


def test_every_bengali_message_keeps_the_english_placeholders(shipped):
    off = [key for key, text in shipped['bn'].items()
           if _fields(text) != _fields(ENGLISH.table[key])]
    assert off == []


def test_the_shipped_file_loads_without_any_notice():
    assert load_languages(DEFAULT_PATH, language='both').notices == ()


def test_the_shipped_file_says_how_to_switch_bengali_on(shipped):
    text = DEFAULT_PATH.read_text(encoding='utf-8')
    assert 'language:' in text and 'both' in text
    assert shipped['language'] in ('en', 'bn', 'both')
