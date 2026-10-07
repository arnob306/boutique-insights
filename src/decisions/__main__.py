"""
Record what was done about a recommendation, and see how they are turning out.

Usage:
    python -m src.decisions record-action --product "Silk Saree" --action ordered --qty 8
    python -m src.decisions summary

Exit codes: 0 ok, 2 bad input or an unusable log.
"""

import argparse
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Callable

from src.decisions.evaluate import summarise
from src.decisions.store import (
    ACTIONS,
    DEFAULT_LOG_PATH,
    Action,
    DecisionLogError,
    add_action,
    latest_recommendation_id,
    open_log,
)

EXIT_OK = 0
EXIT_INPUT = 2


def _say(message: str) -> None:
    sys.stdout.write(message + '\n')


def _parse_args(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='python -m src.decisions')
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--log', type=Path, default=DEFAULT_LOG_PATH,
                        help='the decision log file')
    commands = parser.add_subparsers(dest='command', required=True)

    act = commands.add_parser('record-action', parents=[common],
                              help='note what you did')
    act.add_argument('--product', required=True)
    act.add_argument('--action', required=True, choices=ACTIONS)
    act.add_argument('--qty', required=True, type=int)
    act.add_argument('--date', help='when you did it (YYYY-MM-DD, default today)')

    commands.add_parser('summary', parents=[common],
                        help='how the recommendations turned out')
    return parser.parse_args(argv)


def _record_action(args, today: date) -> int:
    try:
        acted_on = date.fromisoformat(args.date) if args.date else today
    except ValueError:
        _say(f'Cannot record: the date must look like 2026-09-30, not {args.date!r}.')
        return EXIT_INPUT
    try:
        # Built before the log is opened, so bad input never creates the file.
        action = Action(acted_on=acted_on, product=args.product,
                        action=args.action, qty=args.qty)
        with open_log(args.log) as conn:
            linked = latest_recommendation_id(conn, args.product, 'reorder', acted_on)
            add_action(conn, replace(action, recommendation_id=linked))
    except DecisionLogError as exc:
        _say(f'Cannot record: {exc}')
        return EXIT_INPUT
    link = ('linked to the latest reorder recommendation' if linked is not None
            else 'not linked to a recommendation')
    _say(f'Recorded: {args.action} {args.qty} x {args.product} on {acted_on} ({link}).')
    return EXIT_OK


def _summary(args) -> int:
    if not args.log.exists():
        _say(f'No decision log at {args.log} yet. The weekly run creates it.')
        return EXIT_OK
    try:
        with open_log(args.log) as conn:
            rows = summarise(conn)
    except DecisionLogError as exc:
        _say(f'Cannot read the decision log: {exc}')
        return EXIT_INPUT
    if not rows:
        _say('No outcomes yet: a recommendation is scored once its window has passed.')
        return EXIT_OK
    for row in rows:
        error = 'n/a' if row.wape is None else f'{row.wape:.0%}'
        _say(f'{row.model:<14}{row.kind:<10}{row.n:>4} scored   '
             f'error {error:>5}   bias {row.bias:+.1f} units')
    return EXIT_OK


def main(argv=None, *, today: Callable[[], date] = date.today) -> int:
    """Run the command; returns the process exit code."""
    args = _parse_args(argv)
    if args.command == 'record-action':
        return _record_action(args, today())
    return _summary(args)


if __name__ == '__main__':
    sys.exit(main())
