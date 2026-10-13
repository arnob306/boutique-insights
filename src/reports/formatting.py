"""Small wording helpers shared by the report, the dashboard and the playbook."""

CURRENCY = '$'


def money(value: float) -> str:
    sign = '-' if value < 0 else ''
    return f'{sign}{CURRENCY}{abs(value):,.0f}'


def reminder_text(count: int) -> str:
    """The reminder line for the email and dashboard: how many, never who."""
    if count == 0:
        return 'Nobody is due a nudge this week.'
    noun = 'repeat customer is' if count == 1 else 'repeat customers are'
    return (f'{count} {noun} due a nudge. The list is saved on your computer, '
            'not in this email.')


def festival_reminder_line(festival: str, count: int) -> str:
    noun = 'customer' if count == 1 else 'customers'
    return f'{count} {noun} bought at {festival} last year.'
