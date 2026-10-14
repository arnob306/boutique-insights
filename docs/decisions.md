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

## 5. Order quantities by product: built and benchmarked, not shipped as advice

**Context.** The weekly reorder advice adds a flat safety buffer to expected
demand. The newsvendor idea chooses a quantity from the cost of a missed sale
(its margin) against the cost of a leftover (cost less what a leftover later
resells for). It also gives a chance of selling out and an expected leftover,
which the buffer cannot.

**What was built.** A per-product festival forecast (recent ordinary units
times the category's usual lift times the days in the window, the cash plan's
method applied to units), a spread read from past festivals, the quantity,
netting of stock on hand and on order, the supplier's minimum order, and a trim
to a budget that drops the least profitable units first. All of it is in
`src/metrics/ordering*.py` and tested on synthetic data.

**How it was tested.** Every past festival window was run as it would have
been on its order date, six weeks out, using only data from before that date.
Four rules were scored on what actually sold: the plain forecast, the forecast
plus a square-root buffer (the weekly reorder rule's margin), and the
newsvendor quantity with two ways of reading the spread. Scores are margin on
what sold less the cost of what was left, in dollars, over 46 windows and 602
product-festival cases on the owner's real ledger. Stock on the shelf is
ignored, so this compares the gross need.

| Leftovers resell for | Plain forecast | Forecast + buffer | Newsvendor, pooled spread | Newsvendor, size-matched |
|---|---|---|---|---|
| 30% of price | 14,096 (spend 58,508) | 8,627 (86,014) | 10,165 (75,746) | 14,047 (67,826) |
| 50% of price | 23,048 (58,508) | 25,135 (86,014) | 20,841 (181,976) | 25,108 (115,285) |
| 70% of price | 25,949 (58,508) | 30,522 (86,014) | 33,900 (288,870) | 33,676 (149,873) |

Share of demand covered: plain 73.3%, buffer 86.1%, pooled newsvendor 83.2% to
97.2%, size-matched 80.9% to 96.1% (low to high resale).

**What went wrong first.** The first version pooled actual-over-forecast
ratios across every product and size. It lost to the simpler rules at 30% and
50% resale and spent about three times as much at 50%. The cause was that the
spread depends strongly on forecast size: for products forecast under 2 units
the 80th percentile of actual over forecast was 3.9 (347 cases), for 2 to 6
units 1.7 (224), for 6 to 15 units 1.0 (87), and for 15 or more 0.5 (14).
One extra sale is a big ratio on a tiny forecast and a small one on a large
forecast, so the pooled tail overstocked the big sellers. The second version
reads each product's spread from the 60 past cases closest in forecast size.

**Decision.** Do not present newsvendor quantities as order advice. At the
leftover values that are plausible for clothing and jewellery (30% to 50%) the
size-matched rule ties the existing buffer rule on profit and spends 1.3 to 2
times as much. It only wins, by about 10%, when leftovers resell for 70%. The
code stays in the repository as a tested benchmark.

**Caveats.**
- The resale share is not known. It is the input that decides the result, and
  it has to come from the owner.
- 46 windows is small and there are no confidence intervals, so the
  differences are indicative only.
- The size-matched version was designed after seeing the pooled result on the
  same history. Each window is still scored out of sample, but this is not a
  clean pre-registered test.
- The scoring model is the rule's own objective (margin against leftover
  cost). It ignores discounting to clear stock, lost repeat customers and
  cash-flow timing.

**Consequences.** The weekly reorder rule is unchanged. If the owner gives a
real resale share and it is high, the comparison can be rerun on the same
ledger and the decision revisited. Nothing here shows that following any order
rule changes the shop's profit.

## 6. A gradient-boosting challenger beats the live reorder forecast

**Context.** ADR 1 kept the velocity rule because it was the best of three
simple methods, and said no heavier model was needed. That compared simple
rules with each other. It did not test a model trained across products.

**What was tested.** One gradient-boosting model (scikit-learn's
`HistGradientBoostingRegressor` with a Poisson loss, since the target is a
count) is trained across all 15 products at each forecast origin. A row is a
product at a past week. Its features are mean weekly units over the last 4, 13,
26 and 52 normal trading weeks, units in the same weeks a year earlier, and the
week of the year (as a sine and cosine). The target is the units over the next
`horizon` weeks. Rows whose target weeks include a stock-out or lockdown are
dropped, as they are in scoring. The settings were chosen once before the
first run and not tuned. It is scored by the same rolling-origin harness as the
other methods, on the same windows, seeing only weeks before each origin.
`python -m src.forecast --file <workbook> --ml` reproduces it.

**Result** (the owner's real ledger, 613 weeks, 15 products, aggregates only):

| Horizon | Windows | Gradient boosting | Velocity rule (live) | Recent mean | Seasonal naive |
|---|---|---|---|---|---|
| 4 weeks | 1,588 | 59.1% | 66.9% | 68.8% | 75.0% |
| 7 weeks | 898 | 48.6% | 60.5% | 64.8% | 62.0% |

Figures are WAPE, total forecast error over total units sold, lower is
better. Bias was close to zero for the challenger at both horizons (-0.1 and
-0.2 units per window). At the 4-week horizon it had the lowest error in every
one of the 11 years, narrowest in 2017 (71.6% against 72.4%).

**Decision.** Record the result, keep the weekly report on the velocity rule
for now, and treat moving it as an open decision. The gain is large enough to
matter, but using it means the weekly run depends on scikit-learn, retrains
every week, and needs the model's output wired into the reorder maths (which
also applies the festival factor and a safety buffer). That should be its own
change, tested on its own.

**Caveats.**
- One shop, 15 products, and WAPE is dominated by the biggest sellers.
- Two runs (two horizons), no confidence intervals.
- Better forecasts of units do not by themselves mean better orders. The
  order quantity also depends on lead time, minimum orders and the festival
  factor, which this comparison does not test.
- The model has no festival calendar, only the week of the year and last
  year's same weeks, so lunar festivals that move are not captured directly.
  That may be room for improvement, and it has not been explored.

**Consequences.** ADR 1 stands for what it measured (the three simple rules)
but its conclusion that no heavier model was needed is superseded. It is
still not shown that following the better forecast would raise profit.

## 7. The reorder advice uses the model, without the festival factor

**Context.** ADR 6 left open whether better unit forecasts mean better orders,
and whether the festival factor should still be applied on top of the model.

**What was tested.** For every product and origin (a step of 4 weeks, after two
years of history), the order-up-to target was the forecast plus the safety
buffer, as the live code builds it, compared with what sold over the next
lead-time-plus-a-week. Four variants: the live rule (velocity times festival
factor), velocity alone, the model alone, and the model times the festival
factor. Windows with a stock-out or lockdown were left out. Festival windows
were found from the history's own labels, and the factor used only uplifts
learned before each origin.

**Result** (real ledger, 4-week shipment, 1,588 windows, aggregates only):

| Target | WAPE | Fill rate | Leftover units | Units ordered |
|---|---|---|---|---|
| Live rule | 68.6% | 91.1% | 5,682 | 10,893 |
| Velocity, no factor | 63.7% | 86.0% | 4,378 | 9,297 |
| Model alone | 54.4% | 90.2% | 4,151 | 9,311 |
| Model times factor | 67.1% | 93.9% | 5,996 | 11,368 |

Adding the factor to the model made it worse in festival windows (WAPE 54.0%
to 72.2%, bias to +1.7 units), so the model already captures festivals and the
factor counted them twice. The same held at 2 and 6 weeks. Sweeping the
buffer, the model reached the live rule's 93.6% fill with 24% fewer leftover
units (5,399 against 7,063) and 13% fewer units ordered (10,754 against
12,420).

**Decision.** When the model is available the weekly advice uses its forecast
for lead time plus a week, with the same safety buffer and no festival factor.
The festival is still named in the note. The rule is the fallback for a product
the model does not cover, for too little history (under 104 weeks), and when
scikit-learn is not installed. The report never fails because of the model.
Each reorder row carries `rule` or `model`, and the decision log records
`reorder-v1` or `reorder-gbm-v1` so the two can be compared later.

**Caveats.**
- Targets, not orders. The stock sheet is empty, so stock on hand, minimum
  orders and in-transit stock are not modelled in the backtest.
- Units, not dollars. Leftover cost depends on resale value, which is unknown.
- One shop, and the model settings were chosen once, not tuned. The buffer
  sweep was run after seeing the first result.
- Moving lunar festivals may be less well captured than fixed ones. Not
  separated out.
- Retraining each week costs a few seconds and the choice is deterministic
  (fixed seed). Nothing measures yet whether following the advice helps.

## 8. The "Do this week" list can be read in Bengali

**Context.** The report is for a Bengali-speaking owner who is not at ease with
English business wording. The action list is the part she reads first, so it is
the part worth translating.

**Decision.** English wording stays in code (`src/strings.py`) so the report
always has words. Bengali lives in `config/strings.yaml`, so a native speaker
can correct it without touching code. A setting chooses English, Bengali or
both, with the Bengali under each English line. Only the action list, its
headings and notes are translated; product names, dates and the detail sections
stay English. Customer messages are not translated: which language a customer
prefers is not known.

**Safeguards.** A Bengali line that is missing, or whose `{placeholders}` differ
from the English one, falls back to English for that line and is named in a
printed notice, so a typo never breaks or corrupts the weekly run. Tests check
that every English message has a Bengali one with the same placeholders. All
files are written and emailed as UTF-8.

**Caveats.**
- The Bengali is a draft written by an AI. It ships switched off
  (`language: en`) until a native speaker has read it. The tests prove the
  placeholders are right, not that the Bengali is natural.
- No runtime translation. A new English message needs its Bengali added by
  hand, or it shows in English.
- Not measured: whether Bengali helps her act sooner.
