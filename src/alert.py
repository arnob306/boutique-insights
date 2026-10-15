"""
A short email to whoever runs the report when the weekly run fails.

The wording is fixed. It never repeats an error message, a figure, a product
or a customer, because an error can quote a value from the workbook. It only
says what kind of problem it was and what to do next. It goes to ``ALERT_TO``
(the person running the report), never to the shop owner, and it uses the same
mail settings as the report.
"""

import html
import os
from typing import Callable, Dict, Mapping, Optional

from src.reports.delivery import DeliveryError, EmailSettings, build_message

ALERT_VAR = 'ALERT_TO'
SUBJECT = 'Boutique weekly report did not run'

REASONS: Dict[str, str] = {
    'input': ('No report was made. The usual cause is that no new workbook is in the '
              'inbox folder yet (has it been sent?). It can also mean the workbook '
              'could not be read. Run the report by hand to see which.'),
    'untrusted': ('A report was made, but the data has problems, so it was not emailed '
                  'and the workbook was left in the inbox. Run the report by hand to '
                  'see the list of problems.'),
    'delivery': ('The report was made, but emailing it failed. Check the SMTP settings '
                 'in .env, then run the report again.'),
    'crash': ('The program stopped on an unexpected error. Run the report by hand to '
              'see the details.'),
}


def alert_settings(env: Optional[Mapping[str, str]] = None) -> Optional[EmailSettings]:
    """Mail settings addressed to ``ALERT_TO``, or None when no address is set."""
    env = os.environ if env is None else env
    address = (env.get(ALERT_VAR) or '').strip()
    if not address:
        return None
    if '@' not in address:
        raise DeliveryError(f'{ALERT_VAR} must be an email address.')
    return EmailSettings.from_env({**env, 'REPORT_TO': address})


def alert_message(reason: str, settings: EmailSettings):
    if reason not in REASONS:
        raise ValueError(f'Unknown alert reason: {reason}')
    opening = 'The weekly boutique report did not run this time.'
    text = f'{opening}\n\n{REASONS[reason]}\n'
    page = f'<p>{html.escape(opening)}</p><p>{html.escape(REASONS[reason])}</p>'
    return build_message(SUBJECT, text, page, settings)


def send_alert(reason: str, sender: Callable, env: Optional[Mapping[str, str]] = None) -> str:
    """Email the alert and return a one-line note for the console. Never raises on
    a mail problem: a failed alert must not hide the failure it was reporting."""
    try:
        settings = alert_settings(env)
        if settings is None:
            return (f'No alert sent. Set {ALERT_VAR} in .env to be emailed when the '
                    'weekly run fails.')
        sender(settings, alert_message(reason, settings))
    except DeliveryError as exc:
        return f'Could not send the alert: {exc}'
    return f'Alert emailed to the address in {ALERT_VAR}.'
