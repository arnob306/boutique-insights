# Boutique Weekly Summary

A weekly sales report for a small Bengali clothing and jewellery boutique in
Melbourne. It reads the shop's Excel sales workbook, checks the data, and
produces a short plain-English summary (email and a one-page dashboard) that
answers five questions:

1. **What should I reorder?**
2. **Which products actually make money?**
3. **What should I do about each upcoming festival?** An order-by date, which
   categories usually sell faster, and expected sales for the festival with a
   likely range and a stock budget.
4. **Which customers should I contact?** Repeat customers due a nudge, and
   last year's buyers before each festival, in a local file with real names.
5. **Where does the money come from?** Category and channel mix, with margin.

It also keeps a decision log (what was recommended, what happened) and has a
forecast comparison used to check that the live rules are good enough.
It is planning support, not financial advice.

I built it for my mum's real business, so it has a real user. The first
version of this project analysed a public UK retail dataset; that version is
preserved in the git tag `v1-uci-portfolio`.

## Privacy

The shop's real data is never in this repository.

- Real files live in `data/private/`, which is gitignored. A test fails if
  anything under it is ever tracked by git.
- Customer names are replaced with a keyed hash (HMAC-SHA256) inside the
  importer and never reach the email, the dashboard, the decision log or the
  analysis code. Suburb and payment method are dropped there too.
- One deliberate exception: `--reminders` writes a list of customers to
  contact, with real names, to a text file under `data/private/output/` on the
  owner's computer. It is read straight from the workbook by `src/reminders.py`
  only, it is never emailed, and the email and dashboard show a count only.
  Tests check that no name appears anywhere else.
- The weekly report is built from plain arithmetic and templates. No LLM is
  involved, so no business data is sent to a third party.
- The demo, tests and screenshots use a synthetic workbook with invented names
  and perturbed prices (`src/synthetic/`).

## Try the demo

```bash
pip install -r requirements.txt
python -m src.run --profile synthetic
```

This reads `data/synthetic/boutique_synthetic.xlsx` and writes
`reports/demo/weekly_<date>.html` and `.txt`. No secrets are needed.

## Use it on the real workbook

1. Copy `.env.example` to `.env` and set `CUSTOMER_HASH_SALT` (and the `SMTP_*`
   settings if you want the report emailed).
2. Save the emailed workbook into `data/private/inbox/`.
3. Run `python -m src.run --profile private` (add `--send` to email it).

The run prints an import check, writes the report to `data/private/output/`,
and moves the workbook to `data/private/archive/`. If the data has critical
problems the report is marked "not trusted", and it is never emailed or archived.

Add `--dashboard` to also write `dashboard_<date>.html`, a one-page view for a
phone: weekly sales chart, reorder advice, and profit by product. It is a single
file with no scripts or links, built from the same figures as the email, so the
two cannot disagree. With `--send` the dashboard is attached to the email
automatically, so she can open it on her phone.

To run it every week on Windows, once the `.env` is filled in, create a
scheduled task (change the folder to wherever the repo lives):

```
schtasks /Create /SC WEEKLY /D MON /ST 08:00 /TN "Boutique weekly report" ^
  /TR "cmd /c cd /d C:\path\to\boutique-insights && python -m src.run --profile private --send"
```

If no new workbook is in `data/private/inbox/` that morning, the run stops
with a clear message (exit code 2) and sends nothing.

Exit codes: `0` ok, `1` data not trusted, `2` setup or input problem, `3` email failed.

### Workbook layout

| Sheet | Required | Used for |
|---|---|---|
| `Sales` | yes | One row per sale line. Refunds have a Sale ID ending `-R` and a negative quantity. |
| `Buy Prices` | yes | What was paid per item, per product, per year. |
| `Simple Summary` | no | Its totals are checked against the Sales sheet. |
| `Stock & Orders` | no | Switches on reorder advice. |

To create the stock sheet, run
`python -m src.run --profile private --stock-template data/private/stock_template.xlsx`,
fill in the numbers, and copy the sheet into the workbook. Columns: stock on
hand, count date, weeks for a shipment to arrive, minimum order, and what is
already on order.

## How the answers are worked out

- **Profit and margin** use the buy price for the year of each sale. Refunded
  items are assumed to go back on the shelf. Sales with no buy price are left
  out of profit and flagged.
- **Sales velocity** is units per week over the last 13 weeks. Stock-outs and the
  COVID lockdown (read from the `Business Event` labels) are excluded, so a
  product that was unavailable does not look unpopular.
- **Festival effect** is learned from the `Festival / Occasion` labels in the
  shop's own history (for example sarees sell several times faster around Durga
  Puja). Upcoming dates come from `config/festivals.yaml`; check them, because
  several festivals follow the lunar calendar.
- **Reorder suggestion** estimates demand over the shipment time plus a week,
  scaled for any festival in that period, adds a safety buffer, and subtracts
  stock on hand and stock already on order. Products with few recent sales are
  marked as rough guesses.

## Results

Measured on the owner's real ledger (4,993 sales rows, 15 products, 2015 to
2026), which is private, so these cannot be re-run from a clone. The reasoning
and the full tables are in [docs/decisions.md](docs/decisions.md).

- **Reorder forecast.** Over 1,588 rolling 4-week windows the live rule had 66.9%
  error (WAPE), against 68.8% and 75.0% for two alternatives. It was already the
  best of the three, so no heavier model was built.
- **Festival sales forecast.** Across 47 past festival windows, forecast from
  six weeks out, "recent daily sales x the festival's usual lift" had 37% error
  and +7% bias, against 50% to 59% for three alternatives. The playbook shows it
  as a range, because the 10th to 90th percentile of actual over forecast ran
  from 0.62 to 2.45.
- **Customers.** 40% of 2,325 known customers repeat and bring in 71% of revenue,
  which is why the reminder lists exist.
- **Not yet measured:** whether following the advice changed sales or saved
  time. That needs the outcome tracking in the roadmap.

## Project layout

```
src/
  adapters/     read the workbook into the standard schema
  validation/   import checks and the report of problems
  metrics/      profit, velocity, festival patterns, reorder, cash plan, mix
  forecast/     rolling-origin comparison of forecasting methods
  decisions/    SQLite decision log: recommendations, actions, outcomes
  reports/      weekly summary, dashboard, festival playbook, email delivery
  reminders.py  customers to contact (the only code that reads names)
  synthetic/    generator for the demo workbook
  privacy.py    customer hashing
  run.py        command-line entry point
docs/decisions.md       why the main design choices were made
config/festivals.yaml   upcoming festival dates
data/private/           real data (gitignored)
data/synthetic/         demo workbook
reports/demo/           demo report
tests/
```

## Tests

```bash
python -m pytest tests/ --cov=src
python -m ruff check src tests
python -m mypy
```

The suite uses only synthetic data and covers the adapter, validation,
metrics, report, dashboard, delivery, decision log, forecast comparison,
reminders, command line and the privacy guards. CI runs all three commands, with
the tests on Python 3.11 and 3.13.

## Roadmap

- **Done:** import, validation, metrics, weekly summary, email, dashboard,
  decision log, forecast comparison, festival playbook with cash plan,
  customer reminders, mix and channel review.
- **Next:** measure real outcomes. Record each festival forecast and check
  whether its range held, and test whether the festival reminders bring
  customers back, with a small holdout group agreed with the shop's owner.
- **Optional, later:** a plain-English question layer that answers from the
  existing metric functions, only if it never sends customer data to a third
  party.

This project grew out of my earlier e-commerce analytics platform
(dbt, PostgreSQL, Power BI and a RAG layer on the UCI retail dataset), which
lives in its own repo: https://github.com/arnob306/ecommerce-data-storytelling.
