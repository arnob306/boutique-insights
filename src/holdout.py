"""
A fair test of the festival reminders: hold a few customers back on purpose.

With the owner's agreement, a share of each festival's reminder list is not
contacted. Who is held back is decided by a keyed hash of the customer's
pseudonymous id plus the festival and year, so it is reproducible and needs no
names. Afterwards the two groups' purchase rates are compared as they were
assigned (intention to treat: whether she then did contact the others does not
change the groups).

The groups are small (tens of customers per festival), so the honest result in
the first year is usually "inconclusive". The comparison says so, with an
interval and the smallest difference the sample could have detected.
"""

import hashlib
import hmac
import math
from dataclasses import dataclass
from typing import Optional, Set

import pandas as pd

SEND = 'send'
HOLD = 'hold'
MAX_SHARE = 0.5  # holding back more than half would defeat the point of the list
MIN_PER_ARM = 20  # below this in either group, no verdict is given
RECENT_BUYER_DAYS = 28  # someone who just shopped is not on the list
Z_ALPHA = 1.959964  # two-sided 95%
Z_POWER = 0.841621  # 80% power


def _check_share(share: float) -> None:
    if not 0 < share <= MAX_SHARE:
        raise ValueError(
            f'The holdout share must be above 0 and at most {MAX_SHARE}, not {share}.')


def arm_for(customer_id: str, festival: str, year: int, salt: str, share: float) -> str:
    """HOLD for about ``share`` of customers, SEND for the rest; same answer every time."""
    _check_share(share)
    message = f'holdout|{festival}|{year}|{customer_id}'.encode('utf-8')
    digest = hmac.new(salt.encode('utf-8'), message, hashlib.sha256).digest()
    draw = int.from_bytes(digest[:8], 'big') / 2 ** 64
    return HOLD if draw < share else SEND


@dataclass(frozen=True)
class Holdout:
    """Holdout settings for a run; a silly share is refused as soon as it is made."""
    share: float
    salt: str

    def __post_init__(self) -> None:
        _check_share(self.share)

    def arm(self, customer_id: str, festival: str, year: int) -> str:
        return arm_for(customer_id, festival, year, self.salt, self.share)


def eligible_ids(sales: pd.DataFrame, last_start, last_end, listed_on) -> Set[str]:
    """Customers on the festival list: bought in last year's window and not in
    the four weeks up to and including the day the list was made."""
    buys = sales[(sales['quantity'] > 0) & sales['customer_id'].notna()]
    day = pd.to_datetime(buys['date']).dt.normalize()
    listed = pd.Timestamp(listed_on).normalize()
    last = buys[(day >= pd.Timestamp(last_start).normalize())
                & (day <= pd.Timestamp(last_end).normalize())]
    recent = buys[(day >= listed - pd.Timedelta(days=RECENT_BUYER_DAYS)) & (day <= listed)]
    return set(last['customer_id']) - set(recent['customer_id'])


@dataclass(frozen=True)
class Comparison:
    """Send group against held-back group.

    ``difference`` is send rate minus hold rate, with a 95% interval
    (Newcombe's, which stays sensible at 0% and 100%). ``mde`` is the smallest
    difference these group sizes could detect with 80% power.
    """
    n_send: int
    bought_send: int
    n_hold: int
    bought_hold: int
    rate_send: Optional[float]
    rate_hold: Optional[float]
    difference: Optional[float]
    low: Optional[float]
    high: Optional[float]
    mde: Optional[float]
    verdict: str  # inconclusive | positive | negative
    reason: str


def _wilson(bought: int, n: int) -> tuple:
    rate = bought / n
    z2 = Z_ALPHA ** 2
    scale = 1 + z2 / n
    centre = (rate + z2 / (2 * n)) / scale
    half = Z_ALPHA * math.sqrt(rate * (1 - rate) / n + z2 / (4 * n * n)) / scale
    return centre - half, centre + half


def _smallest_detectable(n_send: int, n_hold: int, bought: int) -> float:
    pooled = bought / (n_send + n_hold)
    spread = pooled * (1 - pooled) if 0 < pooled < 1 else 0.25  # 0.25: the cautious case
    return (Z_ALPHA + Z_POWER) * math.sqrt(spread * (1 / n_send + 1 / n_hold))


def _check(n: int, bought: int) -> None:
    if n < 0 or bought < 0 or bought > n:
        raise ValueError('Buyers must be between 0 and the number of customers.')


def compare(n_send: int, bought_send: int, n_hold: int, bought_hold: int) -> Comparison:
    """Compare purchase rates of the contacted and the held-back customers."""
    _check(n_send, bought_send)
    _check(n_hold, bought_hold)
    if n_send == 0 or n_hold == 0:
        return Comparison(n_send, bought_send, n_hold, bought_hold,
                          None, None, None, None, None, None, 'inconclusive',
                          'No customers in one of the groups.')
    rate_send, rate_hold = bought_send / n_send, bought_hold / n_hold
    difference = rate_send - rate_hold
    low_s, high_s = _wilson(bought_send, n_send)
    low_h, high_h = _wilson(bought_hold, n_hold)
    low = difference - math.sqrt((rate_send - low_s) ** 2 + (high_h - rate_hold) ** 2)
    high = difference + math.sqrt((high_s - rate_send) ** 2 + (rate_hold - low_h) ** 2)
    verdict, reason = _verdict(n_send, n_hold, low, high)
    return Comparison(
        n_send, bought_send, n_hold, bought_hold, rate_send, rate_hold, difference,
        low, high, _smallest_detectable(n_send, n_hold, bought_send + bought_hold),
        verdict, reason)


def _verdict(n_send: int, n_hold: int, low: float, high: float) -> tuple:
    if min(n_send, n_hold) < MIN_PER_ARM:
        return 'inconclusive', (
            f'Too few customers so far: at least {MIN_PER_ARM} are needed in each group.')
    if low > 0:
        return 'positive', 'The contacted group bought more, beyond what chance explains.'
    if high < 0:
        return 'negative', 'The contacted group bought less, beyond what chance explains.'
    return 'inconclusive', 'The difference is within what chance could explain.'
