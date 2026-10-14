"""
What to do this week, most urgent first.

One list shared by the email and the dashboard, so the two cannot disagree.
It holds counts and product names only, never customer details. The list is
written once per language the report uses; the first language's action carries
the others as ``also``.
"""

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, List, Optional, Tuple

from src.reports.formatting import festival_reminder_line, reminder_text
from src.reports.playbook import PlaybookItem
from src.reports.stock_signals import OverstockLine
from src.strings import ENGLISH, Voice, say_all
from src.validation.checks import STOCK_STALE_AFTER_DAYS

DAYS_PER_WEEK = 7

if TYPE_CHECKING:
    from src.reports.weekly import ReorderLine, WeeklyReport

URGENCY_RANK = {'now': 0, 'soon': 1, 'info': 2}

# Festival stage -> (how urgent, which message)
FESTIVAL_STAGES = {
    'order_now': ('now', 'festival.order_now'),
    'order_soon': ('soon', 'festival.order_soon'),
    'preview': ('info', 'festival.preview'),
    'on_now': ('info', 'festival.on_now'),
}


@dataclass(frozen=True)
class Action:
    urgency: str  # now | soon | info
    text: str
    word: str = ''  # the urgency word in the text's language; English when empty
    lang: str = 'en'
    also: Tuple['Action', ...] = ()  # the same action in the report's other languages


def _action(voice: Voice, urgency: str, key: str, **params: Any) -> Action:
    return Action(urgency, voice.say(key, **params), voice.urgency(urgency), voice.code)


def action_line(action: Action) -> str:
    """One action as a plain sentence that starts with how urgent it is."""
    return f'{action.word or ENGLISH.urgency(action.urgency)}: {action.text}'


def action_lines(action: Action) -> List[str]:
    """The action in every language of the report, the first language first."""
    return [action_line(action), *(action_line(other) for other in action.also)]


def weeks_text(count: float) -> str:
    return ENGLISH.weeks(count)


def cover_weeks(line: 'ReorderLine') -> Optional[float]:
    return line.stock_now / line.units_per_week if line.units_per_week > 0 else None


def festival_action(item: PlaybookItem, voice: Voice = ENGLISH) -> Optional[Action]:
    stage = FESTIVAL_STAGES.get(item.stage)
    if stage is None:
        return None
    urgency, key = stage
    return _action(voice, urgency, key, festival=item.festival,
                   order_by=f'{item.order_by:%d %b}')


def reorder_action(line: 'ReorderLine', voice: Voice = ENGLISH) -> Action:
    cover = cover_weeks(line)
    head = {'qty': line.order_qty, 'product': line.product}
    if cover is None:
        return _action(voice, 'soon', 'reorder.rough', **head)
    if cover < line.lead_weeks:
        # Whole weeks would hide a near miss ("4 weeks, before a 4-week shipment").
        near_miss = round(cover) >= line.lead_weeks
        return _action(voice, 'now', 'reorder.runs_out', **head,
                       left=voice.weeks(cover, exact=near_miss),
                       lead=voice.weeks(line.lead_weeks))
    return _action(voice, 'soon', 'reorder.lasts', **head, cover=voice.weeks(cover))


def recount_action(r: 'WeeklyReport', voice: Voice = ENGLISH) -> Optional[Action]:
    """Ask for fresh stock counts when the numbers behind the advice are old."""
    if r.recount:
        old = voice.weeks(STOCK_STALE_AFTER_DAYS / DAYS_PER_WEEK)
        return _action(voice, 'soon', 'recount.products',
                       products=', '.join(r.recount), old=old)
    if r.stock_age_days is not None and r.stock_age_days > STOCK_STALE_AFTER_DAYS:
        return _action(voice, 'info', 'recount.sheet',
                       age=voice.weeks(r.stock_age_days / DAYS_PER_WEEK))
    return None


def overstock_action(line: OverstockLine, voice: Voice = ENGLISH) -> Action:
    return _action(voice, 'info', 'overstock', product=line.product,
                   cover=voice.weeks(line.weeks_of_cover), units=line.stock_now)


def _actions_in(r: 'WeeklyReport', voice: Voice) -> List[Action]:
    """Every action for the week in one language, most urgent first."""
    recount = recount_action(r, voice)
    found = [recount] if recount else []
    found += [a for a in (festival_action(item, voice) for item in r.playbook) if a]
    found += [reorder_action(line, voice) for line in r.reorder]
    if r.reminder_count:
        found.append(Action('info', reminder_text(r.reminder_count, voice),
                            voice.urgency('info'), voice.code))
    found += [Action('info', festival_reminder_line(f, n, voice),
                     voice.urgency('info'), voice.code)
              for f, n in r.festival_reminder_counts if n > 0]
    found += [overstock_action(line, voice) for line in r.overstock]
    return sorted(found, key=lambda a: URGENCY_RANK[a.urgency])


def heading(r: 'WeeklyReport') -> str:
    """The section title, in each language of the report."""
    return say_all(r.voices, 'title.do_this_week').replace('\n', ' / ')


def quiet_note(r: 'WeeklyReport') -> str:
    """Why nothing is listed, in each language of the report."""
    def in_voice(voice: Voice) -> str:
        text = voice.say('quiet')
        if r.reorder_status == 'no_stock_sheet':
            text += ' ' + voice.say('quiet.no_stock_sheet')
        return text
    return '\n'.join(dict.fromkeys(in_voice(voice) for voice in r.voices))


def more_note(r: 'WeeklyReport', shown: int, total: int) -> str:
    """How many actions are listed out of all of them, in each language."""
    return say_all(r.voices, 'more', shown=shown, total=total)


def build_actions(r: 'WeeklyReport') -> List[Action]:
    """Every action for the week, most urgent first, in each language of the report."""
    first, *others = (_actions_in(r, voice) for voice in r.voices)
    return [
        replace(action, also=tuple(
            other[i] for other in others if other[i].text != action.text))
        for i, action in enumerate(first)
    ]
