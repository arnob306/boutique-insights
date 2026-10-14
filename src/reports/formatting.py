"""Small wording helpers shared by the report, the dashboard and the playbook."""

import html

from src.strings import ENGLISH, Voice

CURRENCY = '$'


def money(value: float) -> str:
    sign = '-' if value < 0 else ''
    return f'{sign}{CURRENCY}{abs(value):,.0f}'


def reminder_text(count: int, voice: Voice = ENGLISH) -> str:
    """The reminder line for the email and dashboard: how many, never who."""
    if count == 0:
        return voice.say('reminder.none')
    return voice.say('reminder.one' if count == 1 else 'reminder.many', count=count)


def festival_reminder_line(festival: str, count: int, voice: Voice = ENGLISH) -> str:
    key = 'festival_reminder.one' if count == 1 else 'festival_reminder.many'
    return voice.say(key, count=count, festival=festival)


def html_lines(text: str) -> str:
    """Escaped text with each line of it on its own line."""
    return '<br>'.join(html.escape(line.strip()) for line in text.split('\n'))
