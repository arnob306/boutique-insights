import pytest

from src.forecast.__main__ import main


def run(workbook, *args):
    return main(['--file', str(workbook), *args])


def test_synthetic_workbook_prints_a_scoreboard_for_every_method(workbook_path, capsys):
    assert run(workbook_path) == 0
    out = capsys.readouterr().out
    for name in ('velocity rule', 'recent mean', 'seasonal naive'):
        assert name in out
    assert 'WAPE' in out


def test_scoreboard_is_also_split_by_year(workbook_path, capsys):
    run(workbook_path)
    out = capsys.readouterr().out
    assert 'By year' in out
    assert '2025' in out


def test_it_says_how_far_the_current_rule_is_from_the_best_method(workbook_path, capsys):
    run(workbook_path)
    out = capsys.readouterr().out
    assert 'current rule' in out.lower()


def test_no_gap_line_when_the_current_rule_is_not_among_the_methods(
        workbook_path, capsys, monkeypatch):
    from src.forecast import compare
    from src.forecast.methods import recent_mean
    monkeypatch.setattr(compare, 'METHODS', {'recent mean': recent_mean})
    assert run(workbook_path) == 0
    out = capsys.readouterr().out
    assert 'recent mean' in out
    assert 'current rule' not in out.lower()


def test_it_explains_that_stock_outs_and_lockdown_are_left_out(workbook_path, capsys):
    run(workbook_path)
    assert 'stock-out' in capsys.readouterr().out.lower()


def test_horizon_and_spacing_can_be_changed(workbook_path, capsys):
    assert run(workbook_path, '--horizon', '2', '--min-train', '60', '--step', '8') == 0
    assert '2-week' in capsys.readouterr().out


def test_a_missing_file_is_reported_without_a_traceback(tmp_path, capsys):
    assert run(tmp_path / 'nope.xlsx') == 2
    out = capsys.readouterr().out
    assert 'Cannot run' in out
    assert 'Traceback' not in out


def test_a_file_that_is_not_a_workbook_is_reported(tmp_path, capsys):
    broken = tmp_path / 'broken.xlsx'
    broken.write_text('not a workbook', encoding='utf-8')
    assert run(broken) == 2
    out = capsys.readouterr().out
    assert 'broken.xlsx could not be opened as an Excel workbook' in out
    assert 'engine' not in out  # no library jargon
    assert 'Traceback' not in out


def test_a_workbook_without_the_sales_sheet_is_explained(tmp_path, capsys):
    from openpyxl import Workbook
    empty = tmp_path / 'empty.xlsx'
    Workbook().save(empty)
    assert run(empty) == 2
    out = capsys.readouterr().out
    assert 'Cannot run' in out
    assert 'Sales' in out
    assert 'Traceback' not in out


def test_too_little_history_says_how_much_is_needed(workbook_path, capsys):
    assert run(workbook_path, '--min-train', '5000') == 2
    out = capsys.readouterr().out
    assert 'Not enough history' in out
    assert '5004' in out


@pytest.mark.parametrize('flag', ['--horizon', '--min-train', '--step'])
def test_nonsense_settings_are_reported_not_a_traceback(workbook_path, capsys, flag):
    assert run(workbook_path, flag, '0') == 2
    out = capsys.readouterr().out
    assert 'Cannot run' in out
    assert 'Traceback' not in out
