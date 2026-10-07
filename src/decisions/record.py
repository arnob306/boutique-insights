"""
Turn a week's data into decision-log rows.

Two kinds are recorded for the week ending at the newest sale:

- ``forecast``: expected units over the next few weeks, from recent sales
  velocity. It needs no stock sheet, so the log starts filling straight away.
- ``reorder``: what the reorder rule suggested, including "order nothing"
  decisions, so the log can later show whether holding off was right.
"""

from pathlib import Path
from typing import List, Sequence

import pandas as pd

from src.adapters.boutique_xlsx import WorkbookData
from src.decisions.store import Recommendation, add_recommendations, open_log
from src.metrics.patterns import festival_uplift
from src.metrics.reorder import confidence_label, reorder_suggestions
from src.metrics.velocity import weekly_velocity

FORECAST_MODEL = 'velocity-v1'
REORDER_MODEL = 'reorder-v1'
FORECAST_HORIZON_WEEKS = 4


def _forecast_rows(sales: pd.DataFrame, as_of: pd.Timestamp) -> List[Recommendation]:
    velocity = weekly_velocity(sales, as_of).dropna(subset=['units_per_week'])
    return [
        Recommendation(
            as_of=as_of.date(), product=str(row.Index), kind='forecast',
            model=FORECAST_MODEL, horizon_weeks=FORECAST_HORIZON_WEEKS,
            suggested_qty=0,
            expected_demand=float(row.units_per_week) * FORECAST_HORIZON_WEEKS,
            confidence=confidence_label(int(row.sales_count)))
        for row in velocity.itertuples()
    ]


def _reorder_rows(
    data: WorkbookData, as_of: pd.Timestamp, calendar: Sequence
) -> List[Recommendation]:
    if data.stock is None or data.stock.empty:
        return []
    suggestions = reorder_suggestions(
        data.sales, data.stock, as_of, calendar=calendar,
        uplift=festival_uplift(data.sales))
    return [
        Recommendation(
            as_of=as_of.date(), product=str(row.product), kind='reorder',
            model=REORDER_MODEL, horizon_weeks=int(row.lead_weeks) + 1,
            suggested_qty=int(row.order_qty),
            expected_demand=float(row.expected_demand),
            confidence=str(row.confidence))
        for row in suggestions.itertuples()
    ]


def build_recommendations(
    data: WorkbookData, calendar: Sequence = ()
) -> List[Recommendation]:
    """Everything the tool would tell her for the week ending at the newest sale."""
    as_of = data.sales['date'].max().normalize()
    return _forecast_rows(data.sales, as_of) + _reorder_rows(data, as_of, calendar)


def record_week(
    data: WorkbookData, log_path: Path, calendar: Sequence = ()
) -> int:
    """Write the week's recommendations to the log; returns how many were new."""
    recommendations = build_recommendations(data, calendar)
    with open_log(log_path) as conn:
        return add_recommendations(conn, recommendations)
