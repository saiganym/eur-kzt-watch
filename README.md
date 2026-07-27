# Euros to tenge

Watches the EUR→KZT rate at [mig.kz](https://mig.kz/en) (MiG exchange offices, Almaty),
keeps a daily record, publishes a chart, and emails when the rate is worth acting on.

Tracks the **buy** rate — what MiG pays for your euros, which is what you actually
walk out with. Higher is better.

## Setup

**1. Push this to a new public repo.** Public keeps Actions minutes and Pages free.

```bash
git init && git add . && git commit -m "watch the tenge"
git remote add origin git@github.com:YOU/eur-kzt-watch.git
git push -u origin main
```

**2. Turn on the chart page.** Settings → Pages → Source: *Deploy from a branch*,
branch `main`, folder `/docs`. It lands at `https://YOU.github.io/eur-kzt-watch/`.

**3. Add email credentials.** Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `SMTP_USER` | your Gmail address |
| `SMTP_PASS` | a Gmail **app password** — never your account password |
| `MAIL_TO` | where alerts go (defaults to `SMTP_USER`) |
| `SMTP_HOST` | optional, defaults to `smtp.gmail.com` |
| `SMTP_PORT` | optional, defaults to `587` |

App passwords live at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
and need 2-step verification switched on first.

**4. Test it.** Actions → *Check EUR/KZT* → Run workflow, tick **force email**.
That sends one regardless of the rate, so you know SMTP works.

After that it runs itself, three times a day.

## When it emails

An email means *act now*. Two ways a rate qualifies:

**It clears the bar.** 570 ₸ or above — your target. Unconditional.

**It's a genuine local peak.** All three must hold:
- higher than any of the last **21 days**
- at least **1.5%** above the 30-day average
- not in the bottom 45% of the last 90 days

That second rule is the one that catches a good rate below 570. The 90-day floor
is deliberately short, so if the market settles at a new lower level the rules
follow it down instead of going silent for a year.

Then two guards kill repeats: the next email must beat the last one you got by
**3 ₸**, unless **14 days** have passed and the situation is genuinely new.

Backtested over the stored history: 27 emails in 373 days, and it flagged
641.5 ₸ against an actual peak of 643 ₸. In the current below-570 market
(since March) it would have sent 4 — walking up the May rally at 556, 561.5,
567, then 571 when it crossed target.

Check any change before you trust it:

```bash
python3 scripts/backtest.py                    # whole history
python3 scripts/backtest.py --from 2026-03-10  # just the recent market
```

### If you have a date to convert by

Set `convert_by` to the day you need the tenge. Inside the last 90 days the
570 bar slides down toward the recent median, so you stop holding out for a
number that isn't coming:

```json
{ "target_rate": 570, "convert_by": "2026-08-31" }
```

Leave it `null` and the bar stays at 570 forever.

### Every dial

| Setting | Default | What it does |
|---|---|---|
| `target_rate` | 570 | always email at or above this |
| `peak_window_days` | 21 | "higher than it's been" means higher than this many days |
| `min_above_average_pct` | 1.5 | how far above the 30-day average a peak must sit |
| `floor_percentile` / `floor_window_days` | 55 / 90 | ignore rates in the lower half of recent trading |
| `ratchet_kzt` | 3.0 | how much better the next email must be |
| `repeat_after_days` | 14 | after this long, the ratchet lifts |
| `convert_by` | null | deadline that eases the target |
| `min_history_days` | 30 | stay quiet until there's this much data |

Fewer emails: raise `peak_window_days` and `ratchet_kzt`.
More: lower `min_above_average_pct`.

## Running it by hand

```bash
python3 scripts/check_rate.py --dry-run   # verdict only, writes nothing
python3 scripts/backfill.py --months 12   # re-seed history from MiG's archive
```

No dependencies beyond the Python standard library.

## Files

```
scripts/mig.py          scraping and parsing
scripts/check_rate.py   record, judge, email
scripts/backfill.py     seed history from MiG's rate archive
scripts/backtest.py     replay the rules over history before trusting a change
data/rates.csv          one row per day
docs/_template.html     the page design — edit this one
docs/index.html         the built page, data baked in — regenerated each run
docs/data.json          the same data as raw JSON
config.json             thresholds
```

## Worth knowing

- The page carries its own data, so `docs/index.html` opens correctly straight off
  disk — double-click it, no server needed. Edit the design in `_template.html`;
  `index.html` is overwritten on every run.
- History is pre-seeded from MiG's archive endpoint, so the chart works on day one.
- GitHub disables scheduled workflows after 60 days of repository inactivity. The
  bot's own commits normally count, but if alerts go silent for a long stretch,
  check that the workflow is still enabled.
- If mig.kz changes its page structure the scraper raises an error and the run fails
  loudly, rather than quietly recording nonsense. A red X in Actions means go look.
- Rates are Almaty cash-desk rates. Your bank or card will do worse.
