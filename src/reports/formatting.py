"""Small wording helpers shared by the report, the dashboard and the playbook."""

CURRENCY = '$'


def money(value: float) -> str:
    sign = '-' if value < 0 else ''
    return f'{sign}{CURRENCY}{abs(value):,.0f}'
