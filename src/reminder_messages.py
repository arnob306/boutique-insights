"""
Draft messages she can copy into WhatsApp for the customers on her reminders list.

The wording is plain text in ``config/messages.yaml`` so she can change it
without touching code. A message uses only a first name, the festival and its
date. It never quotes what someone spent or bought, and it promises nothing
about prices or stock. These drafts exist only in the local reminders file,
never in the email or dashboard.
"""

import string
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

DEFAULT_PATH = Path('config/messages.yaml')
FALLBACK_NAME = 'there'
TITLES = frozenset({'mr', 'mrs', 'ms', 'miss', 'mx', 'dr', 'shri', 'smt', 'sri'})

FESTIVAL_PLACEHOLDERS = frozenset({'first_name', 'festival', 'date'})
NUDGE_PLACEHOLDERS = frozenset({'first_name'})


@dataclass(frozen=True)
class Templates:
    festival: str
    nudge: str


DEFAULT_TEMPLATES = Templates(
    festival=('Hi {first_name}, {festival} is coming up on {date}. If you would like '
              'anything for it, message me and I will help you choose.'),
    nudge=('Hi {first_name}, it has been a little while! If you are after something '
           'new, message me and I will help you choose.'),
)


def first_name(full_name: Optional[str]) -> str:
    """The name to greet someone by, skipping a title like Mrs or Dr."""
    words = (full_name or '').split()
    while words and words[0].rstrip('.').lower() in TITLES:
        words = words[1:]
    return words[0] if words else FALLBACK_NAME


def _checked(key: str, text: object, allowed: frozenset, source: str) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f'The {key} message in {source} must be some text.')
    try:
        used = {field.split('.')[0].split('[')[0]
                for _, field, _, _ in string.Formatter().parse(text) if field is not None}
    except ValueError:
        raise ValueError(
            f'The {key} message in {source} has a stray curly bracket.') from None
    unknown = sorted(used - allowed)
    if unknown:
        names = ', '.join('{' + name + '}' for name in unknown)
        ok = ', '.join('{' + name + '}' for name in sorted(allowed))
        raise ValueError(
            f'The {key} message in {source} uses {names}, which is not available. '
            f'You can use: {ok}.')
    return text


def load_templates(path=DEFAULT_PATH) -> Templates:
    """Read her wording, falling back to the built-in text for anything not set."""
    path = Path(path)
    if not path.exists():
        return DEFAULT_TEMPLATES
    try:
        loaded = yaml.safe_load(path.read_text(encoding='utf-8'))
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        raise ValueError(
            f'{path.name} could not be read. Check it is plain text with lines '
            'like festival: "Hi {first_name}..."') from None
    if loaded is None:
        return DEFAULT_TEMPLATES
    if not isinstance(loaded, dict):
        raise ValueError(
            f'{path.name} should hold messages like "festival: ..." and "nudge: ...".')
    return Templates(
        festival=_checked('festival', loaded.get('festival', DEFAULT_TEMPLATES.festival),
                          FESTIVAL_PLACEHOLDERS, path.name),
        nudge=_checked('nudge', loaded.get('nudge', DEFAULT_TEMPLATES.nudge),
                       NUDGE_PLACEHOLDERS, path.name),
    )


def festival_message(templates: Templates, name: str, festival: str, start) -> str:
    """A message for someone who shopped at this festival before."""
    return templates.festival.format_map({
        'first_name': first_name(name), 'festival': festival,
        'date': f'{pd.Timestamp(start):%d %b}'})


def nudge_message(templates: Templates, name: str) -> str:
    """A message for a repeat customer who has not been in for a while."""
    return templates.nudge.format_map({'first_name': first_name(name)})
