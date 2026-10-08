"""
Check which forecasting method would have served the boutique best.

Usage:
    python -m src.forecast --file data/private/inbox/Boutique_Sales.xlsx
    python -m src.forecast --file book.xlsx --horizon 4 --min-train 104 --step 4

Prints totals only (never customer data). Exit codes: 0 ok, 2 bad input.
"""

import argparse
import sys
import zipfile
from pathlib import Path

import pandas as pd

from src.adapters.boutique_xlsx import SchemaError, load_workbook_data
from src.forecast.compare import (
    DEFAULT_HORIZON,
    DEFAULT_MIN_TRAIN,
    DEFAULT_STEP,
    run_comparison,
    score,
    score_by_year,
)
from src.forecast.series import WEEK_DAYS, censored_weeks, weekly_demand

# Customer IDs are never used or printed here, but the adapter needs a salt.
UNUSED_SALT = 'forecast-comparison-never-uses-customers'
CURRENT_RULE = 'velocity rule'

EXIT_OK = 0
EXIT_INPUT = 2


class CannotRun(RuntimeError):
    """A problem with the input, shown to the user as-is."""


def _say(message: str = '') -> None:
    sys.stdout.write(message + '\n')


def _parse_args(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog='python -m src.forecast',
        description='Compare forecasting methods on past sales')
    parser.add_argument('--file', required=True, type=Path, help='the sales workbook')
    parser.add_argument('--horizon', type=int, default=DEFAULT_HORIZON,
                        help='weeks ahead to forecast (default %(default)s)')
    parser.add_argument('--min-train', type=int, default=DEFAULT_MIN_TRAIN,
                        help='weeks of history before the first check (default %(default)s)')
    parser.add_argument('--step', type=int, default=DEFAULT_STEP,
                        help='weeks between checks (default %(default)s)')
    return parser.parse_args(argv)


def _load(path: Path):
    try:
        return load_workbook_data(path, salt=UNUSED_SALT)
    except FileNotFoundError:
        raise CannotRun(f'Workbook not found: {path.name}') from None
    except SchemaError as exc:  # a ValueError, so it must come before that one
        raise CannotRun(str(exc)) from None
    except (zipfile.BadZipFile, OSError, ValueError):
        raise CannotRun(f'{path.name} could not be opened as an Excel workbook.') from None


def _board_lines(board: pd.DataFrame) -> list:
    lines = [f'{"Method":<16}{"WAPE":>8}{"Bias":>9}{"Windows":>9}']
    for name, row in board.iterrows():
        wape = 'n/a' if pd.isna(row['wape']) else f'{row["wape"]:.1%}'
        lines.append(f'{name:<16}{wape:>8}{row["bias"]:>+9.1f}{int(row["n"]):>9}')
    return lines


def _gap_line(board: pd.DataFrame) -> str:
    if CURRENT_RULE not in board.index or pd.isna(board.loc[CURRENT_RULE, 'wape']):
        return ''
    best = board.index[0]
    if best == CURRENT_RULE:
        return f'The current rule ({CURRENT_RULE}) is the best of these.'
    gap = (board.loc[CURRENT_RULE, 'wape'] - board.iloc[0]['wape']) * 100
    return f'The current rule ({CURRENT_RULE}) is {gap:.1f} points behind {best}.'


def _report(demand: pd.DataFrame, results: pd.DataFrame, horizon: int) -> None:
    windows = results.drop_duplicates(['target_start', 'product'])
    last_day = demand.index[-1] + pd.Timedelta(days=WEEK_DAYS - 1)
    board = score(results)
    _say(f'{len(demand)} weeks of sales ({demand.index[0]:%d %b %Y} to '
         f'{last_day:%d %b %Y}), {len(demand.columns)} products.')
    _say(f'Each forecast covers the next {horizon}-week window. '
         f'{int(windows["scored"].sum())} of {len(windows)} windows were scored; '
         'stock-out and lockdown weeks are left out.')
    _say()
    for line in _board_lines(board):
        _say(line)
    _say()
    _say('By year (WAPE)')
    _say(score_by_year(results).to_string(
        float_format=lambda value: f'{value:.1%}', na_rep='-'))
    _say()
    gap = _gap_line(board)
    if gap:
        _say(gap)
    _say('WAPE is total forecast error over total units sold (lower is better). '
         'Bias is the average forecast minus actual, in units per window.')


def main(argv=None) -> int:
    """Run the comparison; returns the process exit code."""
    args = _parse_args(argv)
    try:
        data = _load(args.file)
        demand = weekly_demand(data.sales)
        censored = censored_weeks(
            data.sales, pd.DatetimeIndex(demand.index), demand.columns)
        results = run_comparison(
            demand, censored, horizon=args.horizon,
            min_train=args.min_train, step=args.step)
    except (CannotRun, ValueError) as exc:
        _say(f'Cannot run: {exc}')
        return EXIT_INPUT
    if results.empty:
        _say(f'Not enough history: need at least {args.min_train + args.horizon} '
             f'weeks of sales, found {len(demand)}.')
        return EXIT_INPUT
    _report(demand, results, args.horizon)
    return EXIT_OK


if __name__ == '__main__':
    sys.exit(main())
