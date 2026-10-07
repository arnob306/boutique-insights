import pandas as pd
import pytest

from src.adapters.boutique_xlsx import SchemaError
from src.reminders import (
    DUE_WINDOW_DAYS,
    LAPSE_FACTOR,
    MAX_LINES,
    MIN_GAP_DAYS,
    PRIVACY_NOTE,
    build_reminders,
    load_customer_rows,
    render_reminders,
)

TODAY = pd.Timestamp('2026-10-01')
DAY = pd.Timedelta(days=1)


def _rows(*purchases):
    """purchases: (name, days_ago, category, amount[, suburb[, quantity]])"""
    records = []
    for p in purchases:
        name, days_ago, category, amount, *rest = p
        suburb = rest[0] if rest else 'Springvale'
        quantity = rest[1] if len(rest) > 1 else 1
        records.append({
            'date': TODAY - days_ago * DAY, 'name': name, 'suburb': suburb,
            'category': category, 'quantity': quantity, 'amount': amount,
        })
    return pd.DataFrame(records, columns=[
        'date', 'name', 'suburb', 'category', 'quantity', 'amount'])


def _regular(name, ago_first, gap, count, amount=100.0, category='Saree'):
    """A customer who bought ``count`` times, ``gap`` days apart."""
    return [(name, ago_first - gap * i, category, amount) for i in range(count)]


def _build(*purchases):
    return build_reminders(_rows(*purchases), TODAY)


# --- who is due ------------------------------------------------------------

def test_a_customer_past_their_usual_gap_is_due():
    result = _build(*_regular('Asha Test', 80, 30, 2))  # last 50 days ago
    assert [l.name for l in result.due] == ['Asha Test']
    line = result.due[0]
    assert (line.days_since, line.usual_gap_days, line.purchases) == (50, 30, 2)


def test_a_customer_not_yet_at_their_usual_gap_is_not_due():
    result = _build(*_regular('Asha Test', 70, 30, 3))  # last 10 days ago
    assert result.due == () and result.quiet_count == 0


def test_the_due_day_itself_counts():
    result = _build(*_regular('Asha Test', 60, 30, 2))  # last 30 days ago
    assert len(result.due) == 1


def test_a_customer_who_went_quiet_is_counted_but_not_listed():
    quiet_for = LAPSE_FACTOR * 30 + 1
    result = _build(*_regular('Asha Test', quiet_for + 30, 30, 2))
    assert result.due == ()
    assert result.quiet_count == 1


def test_the_last_day_of_the_due_window_is_still_listed():
    result = _build(*_regular('Asha Test', 30 + DUE_WINDOW_DAYS + 30, 30, 2))
    assert len(result.due) == 1


def test_a_customer_long_past_their_due_date_is_not_listed_every_week():
    # 10 days past the window: not due, but not "gone quiet" either
    result = _build(*_regular('Asha Test', 30 + DUE_WINDOW_DAYS + 40, 30, 2))
    assert result.due == () and result.due_count == 0 and result.quiet_count == 0


def test_the_list_only_holds_people_who_became_due_recently():
    result = _build(*_regular('Recent Test', 80, 30, 2, amount=10.0),   # due 20 days ago
                    *_regular('Stale Test', 120, 30, 2, amount=900.0))  # due 60 days ago
    assert [l.name for l in result.due] == ['Recent Test']


def test_a_one_time_customer_is_never_listed():
    result = _build(('Ben Test', 100, 'Saree', 200.0))
    assert result.due == () and result.quiet_count == 0


def test_two_rows_on_one_day_are_one_purchase():
    result = _build(('Ben Test', 100, 'Saree', 200.0),
                    ('Ben Test', 100, 'Bangles', 50.0))
    assert result.due == ()


def test_a_very_short_gap_is_raised_to_the_minimum():
    result = _build(('Cam Test', 60, 'Saree', 10.0), ('Cam Test', 58, 'Saree', 10.0),
                    ('Cam Test', 56, 'Saree', 10.0))
    line = result.due[0]
    assert line.usual_gap_days == MIN_GAP_DAYS
    assert line.days_since == 56


def test_the_usual_gap_is_the_median_not_the_mean():
    # gaps of 30, 30 and 200 days: the median is 30
    result = _build(('Dee Test', 300, 'Saree', 10.0), ('Dee Test', 270, 'Saree', 10.0),
                    ('Dee Test', 240, 'Saree', 10.0), ('Dee Test', 40, 'Saree', 10.0))
    assert result.due[0].usual_gap_days == 30


def test_refunds_do_not_count_as_purchases():
    result = _build(('Eli Test', 80, 'Saree', 100.0),
                    ('Eli Test', 50, 'Saree', -100.0, 'Springvale', -1))
    assert result.due == ()


def test_unnamed_sales_are_ignored():
    rows = _rows(*_regular('Asha Test', 80, 30, 2))
    unnamed = _rows(('x', 80, 'Saree', 5.0), ('x', 50, 'Saree', 5.0))
    unnamed['name'] = [None, float('nan')]
    result = build_reminders(pd.concat([rows, unnamed]), TODAY)
    assert [l.name for l in result.due] == ['Asha Test']


# --- ranking, detail and name clashes ----------------------------------------

def test_the_biggest_spenders_are_listed_first():
    result = _build(*_regular('Small Test', 80, 30, 2, amount=10.0),
                    *_regular('Big Test', 80, 30, 2, amount=500.0))
    assert [l.name for l in result.due] == ['Big Test', 'Small Test']
    assert result.due[0].spend == pytest.approx(1000.0)


def test_the_list_is_capped_but_the_count_is_not():
    purchases = []
    for i in range(MAX_LINES + 5):
        purchases += _regular(f'Person {i:02d} Test', 80, 30, 2, amount=10.0 + i)
    result = _build(*purchases)
    assert len(result.due) == MAX_LINES
    assert result.due_count == MAX_LINES + 5


def test_the_usual_category_is_the_one_they_spend_most_on():
    result = _build(('Fay Test', 80, 'Bangles', 20.0), ('Fay Test', 50, 'Saree', 300.0))
    assert result.due[0].top_category == 'Saree'


def test_name_case_and_spacing_do_not_split_a_customer():
    result = _build(('Gia  Test', 80, 'Saree', 10.0), ('gia test', 50, 'Saree', 10.0))
    assert len(result.due) == 1 and result.due[0].purchases == 2


def test_the_same_name_in_two_suburbs_is_two_customers_and_shows_the_suburb():
    result = _build(*[(n, d, 'Saree', 10.0, s)
                      for n, s in (('Hal Test', 'Clayton'), ('Hal Test', 'Dandenong'))
                      for d in (80, 50)])
    assert sorted(l.suburb for l in result.due) == ['Clayton', 'Dandenong']


def test_the_suburb_is_hidden_when_the_name_is_unique():
    result = _build(*_regular('Ivy Test', 80, 30, 2))
    assert result.due[0].suburb is None


# --- the file she reads -----------------------------------------------------

def test_the_text_lists_names_dates_and_the_usual_category():
    text = render_reminders(_build(*_regular('Asha Test', 80, 30, 2)), TODAY)
    assert 'Asha Test' in text
    assert f'{TODAY - 50 * DAY:%d %b %Y}' in text
    assert 'Saree' in text
    assert '50 days ago' in text


def test_the_text_warns_that_it_holds_personal_details():
    text = render_reminders(_build(*_regular('Asha Test', 80, 30, 2)), TODAY)
    assert PRIVACY_NOTE in text
    assert 'forward' in PRIVACY_NOTE.lower()


def test_the_text_says_so_when_nobody_is_due():
    text = render_reminders(_build(('Ben Test', 100, 'Saree', 5.0)), TODAY)
    assert 'nobody' in text.lower()
    assert PRIVACY_NOTE in text


def test_the_text_mentions_customers_beyond_the_list_and_those_gone_quiet():
    purchases = []
    for i in range(MAX_LINES + 2):
        purchases += _regular(f'Person {i:02d} Test', 80, 30, 2)
    purchases += _regular('Old Test', 300, 30, 2)
    text = render_reminders(_build(*purchases), TODAY)
    assert f'{MAX_LINES + 2} repeat customers' in text
    assert '1 more' in text or 'gone quiet' in text


# --- reading the workbook ---------------------------------------------------

def test_rows_are_read_from_the_workbook_with_names(workbook_path):
    rows = load_customer_rows(workbook_path)
    expected = set(pd.read_excel(workbook_path, sheet_name='Sales')['Customer'].dropna())
    assert expected and set(rows['name'].dropna()) == expected
    assert {'date', 'name', 'suburb', 'category', 'quantity', 'amount'} <= set(rows.columns)


def test_a_workbook_without_a_sales_sheet_is_reported_plainly(tmp_path):
    path = tmp_path / 'no_sales.xlsx'
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({'x': [1]}).to_excel(writer, sheet_name='Other', index=False)
    with pytest.raises(SchemaError, match="Sheet 'Sales' is missing"):
        load_customer_rows(path)


def test_a_workbook_without_a_customer_column_is_reported_plainly(tmp_path):
    path = tmp_path / 'no_customer.xlsx'
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame({'Date': ['2026-01-01']}).to_excel(writer, sheet_name='Sales', index=False)
    with pytest.raises(SchemaError, match='Customer'):
        load_customer_rows(path)
