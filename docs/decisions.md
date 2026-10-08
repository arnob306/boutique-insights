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

## 4. Measure outcomes: forecast coverage and a reminder holdout

**Context.** Accuracy numbers say the forecasts are reasonable. They do not say
that following the advice helped the shop.

**Decision.**
- Each festival's expected sales and range are logged once, while there is
  still time to order (the first one is kept). When the window ends, the actual
  net sales are written next to it, with whether they fell inside the range.
  `python -m src.decisions summary` shows the error, bias and how often the
  range held.
- Optionally (`--reminders --holdout 0.2`, off by default, and only with the
  owner's agreement, because it means not contacting some customers on
  purpose) a share of each festival's eligible customers is left off the
  reminders list. Groups come from a keyed hash of the pseudonymous customer
  id plus festival and year, so they are reproducible and need no names. The
  log stores counts only.
- The two groups are compared as assigned (intention to treat): the share of
  each group that bought from the day after the list to the end of the
  festival. The result is pooled over festivals, with a 95% interval and the
  smallest difference the sample could detect.

**Two choices that differ from the first sketch.**
- The outcome window runs to the end of the festival, not a fixed 30 days,
  because lists go out up to 35 days ahead and most festival purchases would
  fall after a 30-day window.
- The interval is Newcombe's score interval, not a bootstrap from the counts:
  a bootstrap from small counts collapses when a group's rate is 0% or 100%,
  which is likely here. It is deterministic and was checked against the worked
  example in Newcombe (1998).

**Expected result.** The eligible groups are small (tens of customers per
festival), so the first year will probably be inconclusive. No verdict is given
unless each group has at least 20 customers, and the summary says so plainly.

**Consequences.** The log moved to schema version 2 with a non-destructive
upgrade from version 1 (new tables only, tested on a version 1 log that holds
data). Nothing here shows that the advice raised sales until enough festivals
have been scored.
