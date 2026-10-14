"""
The wording of the "Do this week" list, in English and in Bengali.

English lives here, so the report always has words to fall back on. Bengali
lives in ``config/strings.yaml`` under ``bn:`` so it can be corrected without
touching code. A Bengali message that is missing, or that is missing a
placeholder the English one has, quietly falls back to the English wording for
that message and is listed in the notices. No translation happens at run time
and nothing is sent anywhere.
"""

import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import yaml

DEFAULT_PATH = Path('config/strings.yaml')
LANGUAGES = ('en', 'bn', 'both')
DEFAULT_LANGUAGE = 'en'
BENGALI = 'bn'

_ENGLISH_TABLE: Dict[str, str] = {
    'title.do_this_week': 'Do this week',
    'urgency.now': 'Now',
    'urgency.soon': 'Soon',
    'urgency.info': 'FYI',
    'quiet': 'Nothing urgent this week.',
    'quiet.no_stock_sheet': ("Reorder advice is off until the 'Stock & Orders' sheet "
                             'is added to the workbook.'),
    'more': 'The {shown} most urgent of {total}. The rest are in the sections below.',
    'more.dashboard': '{more} more in the sections below.',
    'festival.order_now': '{festival}: order now. The order-by date was {order_by}.',
    'festival.order_soon': '{festival}: order by {order_by}.',
    'festival.preview': ('{festival}: too late to order a shipment in time. '
                         'Sell what is in stock.'),
    'festival.on_now': '{festival} is on. Keep an eye on stock.',
    'reorder.rough': ('Order {qty} {product}: no recent sales, so treat this as a '
                      'rough guess.'),
    'reorder.runs_out': ('Order {qty} {product}: stock runs out in {left}, before a '
                         'shipment ({lead}) arrives.'),
    'reorder.lasts': 'Order {qty} {product}: stock lasts {cover}.',
    'recount.products': ('Recount these first: {products}. Their stock numbers are '
                         'over {old} old, so the order advice for them may be off.'),
    'recount.sheet': ('Stock was last counted {age} ago. Recount a few products and '
                      'update the sheet.'),
    'overstock': ('{product}: about {cover} of stock ({units} left) at the current '
                  'pace. Consider a sale to free up cash.'),
    'reminder.none': 'Nobody is due a nudge this week.',
    'reminder.one': ('{count} repeat customer is due a nudge. The list is saved on '
                     'your computer, not in this email.'),
    'reminder.many': ('{count} repeat customers are due a nudge. The list is saved on '
                      'your computer, not in this email.'),
    'festival_reminder.one': '{count} customer bought at {festival} last year.',
    'festival_reminder.many': '{count} customers bought at {festival} last year.',
    'weeks.one': '{n} week',
    'weeks.many': '{n} weeks',
}


@dataclass(frozen=True, eq=False)
class Voice:
    """One language's wording."""

    code: str
    table: Mapping[str, str]

    def say(self, key: str, **params: Any) -> str:
        return self.table[key].format_map(params)

    def weeks(self, count: float, exact: bool = False) -> str:
        """A number of weeks. ``exact`` keeps one decimal and reads as plural."""
        if exact:
            return self.say('weeks.many', n=f'{count:.1f}')
        whole = round(count)
        return self.say('weeks.one' if whole == 1 else 'weeks.many', n=whole)

    def urgency(self, urgency: str) -> str:
        return self.table[f'urgency.{urgency}']


ENGLISH = Voice('en', _ENGLISH_TABLE)


def say_all(voices: Sequence[Voice], key: str, **params: Any) -> str:
    """The message in each voice, one line each, skipping a line that repeats."""
    return '\n'.join(dict.fromkeys(voice.say(key, **params) for voice in voices))


@dataclass(frozen=True)
class Languages:
    """The voices a report is written in, in order, and what to tell the user."""

    voices: Tuple[Voice, ...]
    notices: Tuple[str, ...] = ()


def _fields(text: str) -> frozenset:
    return frozenset(
        field.split('.')[0].split('[')[0]
        for _, field, _, _ in string.Formatter().parse(text) if field is not None)


def _problem(text: object, english: str) -> Optional[str]:
    """Why this Bengali message cannot be used in place of the English one."""
    if not isinstance(text, str) or not text.strip():
        return 'must be some text'
    try:
        used = _fields(text)
    except ValueError:
        return 'has a stray curly bracket'
    wanted = _fields(english)
    if used == wanted:
        return None
    names = ', '.join('{' + name + '}' for name in sorted(wanted)) or 'no {placeholders}'
    return f'must use {names}'


def _read(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        raise ValueError(
            f'{path.name} could not be read. Check it is plain text with lines '
            'like language: both') from None
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f'{path.name} should hold "language: ..." and a "bn:" section '
                         'of messages.')
    return loaded


def _bengali_table(raw: object, source: str) -> Tuple[Dict[str, str], List[str], int]:
    """English wording overlaid with every usable Bengali message, plus notices."""
    if raw is None:
        return dict(_ENGLISH_TABLE), [], 0
    if not isinstance(raw, dict):
        raise ValueError(f'The bn section of {source} should be a list of messages '
                         'like "urgency.now: ...".')
    table, notices, used = dict(_ENGLISH_TABLE), [], 0
    for key, text in raw.items():
        if key not in _ENGLISH_TABLE:
            notices.append(f'{source}: {key} is not a message the report uses, so it '
                           'was ignored.')
            continue
        problem = _problem(text, _ENGLISH_TABLE[key])
        if problem:
            notices.append(f'{source}: {key} {problem}, so the English wording is '
                           'used for it.')
            continue
        table[key], used = text, used + 1
    return table, notices, used


def load_languages(path=DEFAULT_PATH, language: Optional[str] = None) -> Languages:
    """Read the language setting and Bengali wording; ``language`` overrides the file."""
    path = Path(path)
    loaded = _read(path)
    chosen = language if language is not None else loaded.get('language', DEFAULT_LANGUAGE)
    if chosen not in LANGUAGES:
        raise ValueError(f'The language must be one of: en, bn or both (got {chosen!r}).')
    table, notices, used = _bengali_table(loaded.get('bn'), path.name)
    if chosen == 'en':
        return Languages((ENGLISH,), tuple(notices))
    if not used:
        notices.append(f'No Bengali wording found in {path.name}, so reports are '
                       'English only.')
        return Languages((ENGLISH,), tuple(notices))
    bengali = Voice(BENGALI, table)
    voices = (bengali,) if chosen == 'bn' else (ENGLISH, bengali)
    return Languages(voices, tuple(notices))
