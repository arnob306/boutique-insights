# Design decisions

Short records of why the main choices were made, with the evidence behind
them. Numbers come from the owner's real ledger (4,993 sales rows, 15 products,
January 2015 to September 2026), which is not in this repository, so they
cannot be re-run from a clone. Everything else here can be.

## 1. Keep the simple velocity rule for reorder forecasts

**Context.** Per-product demand is intermittent: the median product has no
sales in 59% of the weeks it is on sale. The question was whether a more
complex forecaster (Croston, negative binomial) was worth building.

**Evidence.** A rolling-origin comparison (`python -m src.forecast`) scored three
methods on 4-week windows, using only data from before each origin and leaving
out stock-out and lockdown weeks. Of 1,905 windows, 1,588 were scored.

| Method | WAPE | Bias |
|---|---|---|
| Velocity rule (13-week mean, the live rule) | 66.9% | none |
| Recent mean (8 weeks) | 68.8% | none |
| Seasonal naive (52 weeks back) | 75.0% | none |

The differences are about 2 points. With only 2 to 3 units sold per product
per window, even a perfect forecast would probably miss by a large margin
(a back-of-envelope estimate, not a measured figure).

**Decision.** Keep the velocity rule. Do not build the heavier models.

**Consequences.** Effort goes to adoption (the stock sheet) and to measuring
outcomes. Revisit when the decision log has a year of real outcomes.

## 2. Names stay out of the pipeline, with one local exception

**Context.** The repository is public and the ledger has customer names.
Repeat customers are the most useful thing to act on (40% of 2,325 known
customers repeat and bring in 71% of revenue), which needs names.

**Decision.**
- The importer replaces names with a keyed hash (HMAC-SHA256, secret in `.env`)
  and drops suburb and payment method. The analysis code only sees the hash.
- The decision log has no customer or free-text columns, because free text is
  where names leak in.
- `src/reminders.py` is the only code that reads names. It writes them to one
  local file under `data/private/output/`. The email and dashboard carry a
  count only.
- Tests check that no name reaches the email, the dashboard, the console or the
  decision log, and that nothing under `data/private/` is tracked by git.

**Consequences.** The reminders file is the one place with personal data, so it
carries a "do not forward" note and is never attached to the email.

## 3. Forecast festival sales as baseline times lift, and show a range

**Context.** She needs to decide how much stock to commit before a festival,
about six weeks ahead.

**Evidence.** Four methods were backtested on 47 past festival windows, each
seeing only data from before its order date:

| Method | WAPE | Bias |
|---|---|---|
| Recent ordinary daily sales x the festival's usual lift x days | 37% | +7% |
| Last year's window sales | 50% | +4% |
| Recent ordinary daily sales only | 50% | -50% |
| Last year's window x recent growth | 59% | +16% |

Actual over forecast across those windows had a 10th to 90th percentile of
0.62 to 2.45.

**Decision.** Use the first method. Show the expected figure with the 10th to
90th percentile range, and a stock budget from her cost share. Decline to plan
with fewer than 2 past windows, data more than 14 days old, or a baseline made
mostly of festival days. With fewer than 8 backtest windows, show the expected
figure without a range.

**Consequences.** The range is wide because festival sales really are uneven,
and it is shown as a range for that reason. Lunar festival dates are estimates.
It is planning support, not financial advice.

## 4. Measure outcomes, starting with a reminder holdout (proposed)

**Context.** Accuracy numbers say the forecasts are reasonable. They do not say
that following the advice helped the shop.

**Decision (proposed, not yet built).**
- Record each festival forecast near its order-by date and, once the window
  ends, whether the actual sales fell inside the range.
- Optionally hold back about 20% of the festival reminder list, assigned by a
  keyed hash so it is reproducible and needs no names, and compare repeat
  purchases within 30 days. This is off by default and needs the owner's
  agreement, because it means not contacting some customers on purpose.

**Expected result.** The eligible groups are small (tens of customers per
festival), so the first year will probably be inconclusive. The report should
say so plainly, with a confidence interval and the smallest difference the
sample could detect.
