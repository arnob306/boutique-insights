"""
Run the weekly pipeline: import the workbook, check it, build the report.

Usage:
    python -m src.run --profile synthetic            # demo, no secrets needed
    python -m src.run --profile private              # newest file in the inbox
    python -m src.run --profile private --send       # ...and email the report
    python -m src.run --profile private --reminders  # ...and list customers due a nudge

--reminders writes a local file with real customer names to data/private/output/.
The email and dashboard only ever say how many are due.

A private run also adds the week's recommendations to the decision log
(data/private/decisions.sqlite); --decision-log PATH picks another file.

Exit codes: 0 ok, 1 data not trusted, 2 setup or input problem, 3 email failed.
"""

import argparse
import logging
import shutil
import sys
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import NamedTuple, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv

from src.adapters.boutique_xlsx import SchemaError, load_workbook_data
from src.decisions.evaluate import evaluate_log
from src.decisions.record import record_week
from src.decisions.store import DecisionLogError
from src.metrics.calendar import DEFAULT_CALENDAR_PATH, load_calendar
from src.metrics.patterns import festival_windows
from src.metrics.trends import weekly_series
from src.privacy import PrivacyError, get_salt
from src.reminders import (
    FestivalList,
    Reminders,
    build_festival_reminders,
    build_reminders,
    load_customer_rows,
    write_reminders,
)
from src.reports.dashboard import WEEKS_SHOWN, render_dashboard
from src.reports.delivery import (
    DeliveryError,
    EmailSettings,
    build_message,
    send_email,
)
from src.reports.weekly import (
    build_weekly_report,
    render_html,
    render_text,
    subject,
)
from src.stock_template import write_stock_template
from src.validation.checks import validate_import

logger = logging.getLogger(__name__)

DEFAULT_PRIVATE_ROOT = Path('data/private')
SYNTHETIC_WORKBOOK = Path('data/synthetic/boutique_synthetic.xlsx')
DEFAULT_DEMO_OUTPUT = Path('reports/demo')
DECISION_LOG_NAME = 'decisions.sqlite'
LOCK_PREFIX = '~$'  # Excel's temporary file while a workbook is open
DEMO_SALT = 'synthetic-demo-salt-not-a-secret'

EXIT_OK = 0
EXIT_UNTRUSTED = 1
EXIT_INPUT = 2
EXIT_DELIVERY = 3


class InputError(RuntimeError):
    """A problem with the workbook or setup, shown to the user as-is."""


def _say(message: str) -> None:
    sys.stdout.write(message + '\n')


def _parse_args(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Weekly boutique report')
    parser.add_argument('--profile', choices=['private', 'synthetic'],
                        default='private')
    parser.add_argument('--file', type=Path, help='use this workbook')
    parser.add_argument('--send', action='store_true',
                        help='email the report when the data is trusted')
    parser.add_argument('--dashboard', action='store_true',
                        help='also write a one-page dashboard')
    parser.add_argument('--private-root', type=Path, default=DEFAULT_PRIVATE_ROOT)
    parser.add_argument('--demo-output', type=Path, default=DEFAULT_DEMO_OUTPUT)
    parser.add_argument('--decision-log', type=Path, metavar='PATH',
                        help='record recommendations here (private profile '
                             'defaults to <private-root>/decisions.sqlite)')
    parser.add_argument('--reminders', action='store_true',
                        help='also list repeat customers due a nudge, with their '
                             'names, in a local file (private profile only)')
    parser.add_argument('--today', help='override today (YYYY-MM-DD)')
    parser.add_argument('--stock-template', type=Path, metavar='PATH',
                        help='write a blank Stock & Orders sheet and stop')
    return parser.parse_args(argv)


def _find_workbook(args) -> Path:
    if args.file:
        return args.file
    if args.profile == 'synthetic':
        return SYNTHETIC_WORKBOOK
    inbox = args.private_root / 'inbox'
    found = sorted(
        (p for p in inbox.glob('*.xlsx') if not p.name.startswith(LOCK_PREFIX)),
        key=lambda p: p.stat().st_mtime,
    ) if inbox.exists() else []
    if not found:
        raise InputError(
            f'No workbook found in the inbox ({inbox}). '
            'Save the emailed .xlsx file there and run again.')
    return found[-1]


def _salt_for(profile: str) -> str:
    return DEMO_SALT if profile == 'synthetic' else get_salt()


def _write_outputs(out_dir: Path, report) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f'weekly_{report.data_through:%Y-%m-%d}'
    html_path = out_dir / f'{stem}.html'
    html_path.write_text(render_html(report), encoding='utf-8')
    (out_dir / f'{stem}.txt').write_text(render_text(report), encoding='utf-8')
    return html_path


def _dashboard_page(report, sales) -> Tuple[str, str]:
    """The dashboard's file name and HTML."""
    series = weekly_series(sales, report.data_through, WEEKS_SHOWN)
    name = f'dashboard_{report.data_through:%Y-%m-%d}.html'
    return name, render_dashboard(report, series)


def _write_dashboard(out_dir: Path, report, sales) -> Path:
    name, page = _dashboard_page(report, sales)
    path = out_dir / name
    path.write_text(page, encoding='utf-8')
    return path


def _free_name(path: Path) -> Path:
    """``path``, or ``path`` with a counter if that name is already taken."""
    candidate, counter = path, 2
    while candidate.exists():
        candidate = path.with_name(f'{path.stem}_{counter}{path.suffix}')
        counter += 1
    return candidate


def _archive(workbook: Path, private_root: Path, today: pd.Timestamp) -> Optional[Path]:
    if workbook.parent != private_root / 'inbox':
        return None
    archive = private_root / 'archive'
    archive.mkdir(parents=True, exist_ok=True)
    target = _free_name(archive / f'{today:%Y-%m-%d}_{workbook.name}')
    shutil.move(str(workbook), target)
    return target


def _load(workbook: Path, salt: str):
    try:
        return load_workbook_data(workbook, salt=salt)
    except FileNotFoundError:
        raise InputError(f'Workbook not found: {workbook.name}') from None
    except SchemaError as exc:  # a ValueError, so it must come before that one
        raise InputError(str(exc)) from None
    except (zipfile.BadZipFile, OSError, ValueError):
        raise InputError(
            f'{workbook.name} could not be opened as an Excel workbook.') from None


class ReminderPack(NamedTuple):
    """Both reminder lists; they hold names and so never leave this module's
    caller except as counts."""
    gap: Reminders
    festivals: Tuple[FestivalList, ...]


def _load_reminders(args, workbook: Path, today: pd.Timestamp,
                    sales: pd.DataFrame) -> Optional[ReminderPack]:
    """Who is due a nudge, read straight from the workbook (names stay here)."""
    if not args.reminders:
        return None
    try:
        rows = load_customer_rows(workbook)
    except SchemaError as exc:  # a ValueError, so it must come before that one
        raise InputError(str(exc)) from None
    except (zipfile.BadZipFile, OSError, ValueError):
        raise InputError(
            f'{workbook.name} could not be read for the reminders list.') from None
    festivals = build_festival_reminders(
        rows, _calendar(), festival_windows(sales), today)
    return ReminderPack(build_reminders(rows, today), festivals)


def _with_reminder_counts(report, pack: Optional[ReminderPack]):
    """The report with how many customers are listed, never who."""
    if pack is None or not report.trusted:
        return report
    return replace(
        report, reminder_count=pack.gap.due_count,
        festival_reminder_counts=tuple((f.festival, f.count) for f in pack.festivals))


def _write_reminders(args, pack: Optional[ReminderPack], today: pd.Timestamp) -> None:
    if pack is None:
        return
    path = write_reminders(pack.gap, args.private_root / 'output', today, pack.festivals)
    _say(f'Reminders list written to {path} (it has customer names: keep it local).')


def _email(report, sales, sender) -> None:
    settings = EmailSettings.from_env()
    message = build_message(subject(report), render_text(report),
                            render_html(report), settings,
                            attachments=(_dashboard_page(report, sales),))
    sender(settings, message)


def _calendar() -> list:
    return (load_calendar(DEFAULT_CALENDAR_PATH)
            if DEFAULT_CALENDAR_PATH.exists() else [])


def _build_report(data, today):
    validation = validate_import(data, as_of=today)
    _say(validation.to_text())
    return build_weekly_report(data, validation, today=today, calendar=_calendar())


def _decision_log_path(args) -> Optional[Path]:
    """Where to log this run's recommendations, or None for no log."""
    if args.decision_log:
        return args.decision_log
    if args.profile == 'private':
        return args.private_root / DECISION_LOG_NAME
    return None


def _record_decisions(args, data, today: pd.Timestamp) -> None:
    """Score old recommendations and log this week's; a log problem never
    stops the report."""
    path = _decision_log_path(args)
    if path is None:
        return
    try:
        scored = evaluate_log(data.sales, path, today.date())
        added = record_week(data, path, calendar=_calendar())
    except DecisionLogError as exc:
        _say(f'Decision log not updated: {exc}')
        return
    _say(f'Decision log: {added} new recommendation(s) recorded, '
         f'{scored} outcome(s) scored.')


def _write_all(args, report, sales) -> None:
    out_dir = (args.private_root / 'output' if args.profile == 'private'
               else args.demo_output)
    _say(f'Report written to {_write_outputs(out_dir, report)}')
    if args.dashboard:
        _say(f'Dashboard written to {_write_dashboard(out_dir, report, sales)}')


def main(argv=None, *, sender=send_email, env_file: Optional[str] = '.env') -> int:
    """Run the pipeline; returns the process exit code."""
    logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
    if env_file:
        load_dotenv(env_file)
    args = _parse_args(argv)
    if (args.profile == 'synthetic' and args.file
            and args.demo_output == DEFAULT_DEMO_OUTPUT):
        _say('Cannot run: with --profile synthetic and --file, also give '
             '--demo-output so the report is not written into the repo '
             f'folder ({DEFAULT_DEMO_OUTPUT}).')
        return EXIT_INPUT
    if args.reminders and args.profile != 'private':
        _say('Cannot run: --reminders lists real customer names, so it only '
             'works with --profile private.')
        return EXIT_INPUT
    today = pd.Timestamp(args.today or pd.Timestamp.today()).normalize()

    try:
        workbook = _find_workbook(args)
        data = _load(workbook, _salt_for(args.profile))
        reminders = _load_reminders(args, workbook, today, data.sales)
    except (InputError, PrivacyError) as exc:
        _say(f'Cannot run: {exc}')
        return EXIT_INPUT

    if args.stock_template:
        path = write_stock_template(data.sales, args.stock_template, today)
        _say(f'Stock template written to {path}')
        return EXIT_OK

    report = _build_report(data, today)
    report = _with_reminder_counts(report, reminders)
    _write_all(args, report, data.sales)

    if not report.trusted:
        _say('The data has problems, so the report was not emailed or archived.')
        return EXIT_UNTRUSTED
    _record_decisions(args, data, today)
    _write_reminders(args, reminders, today)
    if args.send:
        try:
            _email(report, data.sales, sender)
        except DeliveryError as exc:
            _say(f'Cannot send the email: {exc}')
            return EXIT_DELIVERY
        _say('Report emailed.')
    if args.profile == 'private':
        archived = _archive(workbook, args.private_root, today)
        if archived:
            _say(f'Workbook archived as {archived.name}')
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
