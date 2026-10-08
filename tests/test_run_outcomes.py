"""The weekly run records festival forecasts and scores them later."""

import shutil

import pytest

from src.decisions.store import open_log
from src.run import main

TODAY = '2026-10-01'
SALT = 'unit-test-salt-0123456789'


@pytest.fixture
def private_root(tmp_path):
    root = tmp_path / 'private'
    (root / 'inbox').mkdir(parents=True)
    return root


@pytest.fixture
def with_salt(monkeypatch):
    monkeypatch.setenv('CUSTOMER_HASH_SALT', SALT)


@pytest.fixture
def inbox_file(private_root, workbook_path):
    target = private_root / 'inbox' / 'Boutique_Sales.xlsx'
    shutil.copy(workbook_path, target)
    return target


def _private(private_root, *extra, today=TODAY):
    return main(['--today', today, '--profile', 'private',
                 '--private-root', str(private_root), *extra], env_file=None)


def _rows(private_root, sql):
    with open_log(private_root / 'decisions.sqlite') as conn:
        return conn.execute(sql).fetchall()


# --- festival forecasts ------------------------------------------------------

def test_a_private_run_records_the_festival_forecast_to_order_for(
        private_root, inbox_file, with_salt):
    assert _private(private_root) == 0
    rows = _rows(private_root, 'SELECT festival, expected FROM festival_forecasts')
    assert [r[0] for r in rows] == ['Diwali / Kali Puja']
    assert rows[0][1] > 0


def test_running_again_keeps_the_first_festival_forecast(
        private_root, inbox_file, with_salt, workbook_path):
    _private(private_root)
    first = _rows(private_root, 'SELECT recorded_on, expected FROM festival_forecasts')
    shutil.copy(workbook_path, private_root / 'inbox' / 'Boutique_Sales.xlsx')
    _private(private_root, today='2026-10-02')
    assert _rows(private_root, 'SELECT recorded_on, expected FROM festival_forecasts') == first


def test_untrusted_data_records_no_festival_forecast(
        private_root, with_salt, make_workbook):
    shutil.copy(make_workbook(problems={'duplicate_id'}),
                private_root / 'inbox' / 'bad.xlsx')
    assert _private(private_root) == 1
    assert not (private_root / 'decisions.sqlite').exists()


def test_a_forecast_whose_window_has_passed_is_scored_and_reported(
        private_root, inbox_file, with_salt, capsys):
    with open_log(private_root / 'decisions.sqlite') as conn:
        conn.execute(
            'INSERT INTO festival_forecasts (festival, window_start, window_end, '
            "recorded_on, expected, low, high, lift_windows, backtest_windows) "
            "VALUES ('Durga Puja', '2026-08-10', '2026-08-21', '2026-06-20', "
            '1000, 500, 2000, 4, 12)')
    assert _private(private_root) == 0
    rows = _rows(private_root, 'SELECT actual, inside_range FROM festival_outcomes')
    assert len(rows) == 1
    assert rows[0][0] > 0
    assert 'festival forecast' in capsys.readouterr().out.lower()


# --- reminder holdout ----------------------------------------------------------

def _holdout_run(private_root, share='0.5', today=TODAY):
    return _private(private_root, '--reminders', '--holdout', share, today=today)


def test_holdout_needs_reminders(private_root, inbox_file, with_salt, capsys):
    assert _private(private_root, '--holdout', '0.2') == 2
    assert '--holdout' in capsys.readouterr().out


@pytest.mark.parametrize('share', ['0', '0.9', '-0.2'])
def test_a_silly_share_is_refused_without_a_traceback(
        private_root, inbox_file, with_salt, capsys, share):
    assert _holdout_run(private_root, share) == 2
    out = capsys.readouterr().out
    assert 'share' in out and 'Traceback' not in out
    assert not list((private_root / 'output').glob('reminders_*'))


def test_holdout_is_off_unless_asked_for(private_root, inbox_file, with_salt):
    assert _private(private_root, '--reminders') == 0
    assert _rows(private_root, 'SELECT COUNT(*) FROM experiments') == [(0,)]
    assert 'held back' not in next(
        (private_root / 'output').glob('reminders_*')).read_text(encoding='utf-8')


def test_a_holdout_run_records_the_experiment_and_says_so_in_the_file(
        private_root, inbox_file, with_salt):
    assert _holdout_run(private_root) == 0
    rows = _rows(private_root, 'SELECT festival, holdout_share, n_send, n_hold '
                               'FROM experiments')
    assert rows and all(share == 0.5 and hold > 0 for _, share, _, hold in rows)
    text = next((private_root / 'output').glob('reminders_*')).read_text(encoding='utf-8')
    assert 'held back on purpose' in text


def test_the_holdout_adds_no_names_to_the_log(private_root, inbox_file, with_salt,
                                              workbook_path):
    from src.reminders import load_customer_rows
    names = {str(n) for n in load_customer_rows(workbook_path)['name'].dropna()}
    assert _holdout_run(private_root) == 0
    log = (private_root / 'decisions.sqlite').read_bytes().decode('latin-1')
    assert not any(name in log for name in names)


def test_a_second_run_keeps_the_first_assignment(private_root, inbox_file, with_salt,
                                                 workbook_path):
    _holdout_run(private_root)
    first = _rows(private_root, 'SELECT listed_on, n_send, n_hold FROM experiments')
    shutil.copy(workbook_path, private_root / 'inbox' / 'Boutique_Sales.xlsx')
    _holdout_run(private_root, today='2026-10-02')
    assert _rows(private_root, 'SELECT listed_on, n_send, n_hold FROM experiments') == first
