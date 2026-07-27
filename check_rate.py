#!/usr/bin/env python3
"""Check the EUR->KZT rate at mig.kz, record it, and email only when it's worth acting on.

Run by GitHub Actions on a schedule. Locally: python3 scripts/check_rate.py --dry-run
"""

import argparse
import csv
import json
import os
import smtplib
import ssl
import sys
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import mig  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "data" / "rates.csv"
STATE_PATH = ROOT / "data" / "state.json"
CONFIG_PATH = ROOT / "config.json"
FIELDS = ["date", "close", "high", "low", "sell", "nbk"]

DEFAULTS = {
    "target_rate": 570,            # the rate you'd happily convert at
    "peak_window_days": 21,        # "higher than it's been" means higher than this many days
    "min_above_average_pct": 1.5,  # and this far above the recent average
    "average_window_days": 30,
    "floor_percentile": 55,        # never flag a rate in the bottom half of recent trading
    "floor_window_days": 90,       # ...where "recent" means this long — short enough to
                                   # adapt when the market resets to a new, lower level
    "ratchet_kzt": 3.0,            # next email must beat the last one by this much
    "repeat_after_days": 14,       # ...unless this long has passed
    "convert_by": None,            # optional "YYYY-MM-DD"; eases the target as it nears
    "min_history_days": 30,        # stay quiet until there's enough history to judge
}


# ---------- storage ----------

def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text()))
    return cfg


def load_history():
    if not CSV_PATH.exists():
        return []
    rows = []
    with CSV_PATH.open() as f:
        for r in csv.DictReader(f):
            rows.append({
                "date": r["date"],
                "close": float(r["close"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "sell": float(r["sell"]) if r.get("sell") else None,
                "nbk": float(r["nbk"]) if r.get("nbk") else None,
            })
    return sorted(rows, key=lambda r: r["date"])


def save_history(rows):
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CSV_PATH.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in sorted(rows, key=lambda x: x["date"]):
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in FIELDS})


def record_today(history, live):
    """Merge the current quote into today's row, keeping the intraday high."""
    today = (live.get("taken_at") or datetime.now().isoformat())[:10]
    buy = live["buy"]
    by_date = {r["date"]: r for r in history}
    row = by_date.get(today)
    if row:
        row["close"] = buy
        row["high"] = max(row["high"], buy)
        row["low"] = min(row["low"], buy)
        row["sell"] = live.get("sell")
        row["nbk"] = live.get("nbk")
    else:
        by_date[today] = {"date": today, "close": buy, "high": buy, "low": buy,
                          "sell": live.get("sell"), "nbk": live.get("nbk")}
    return sorted(by_date.values(), key=lambda r: r["date"]), today


def write_site_data(history, live, verdict, cfg):
    """Build the chart page with its data baked in.

    The data is inlined rather than fetched so the page works when opened
    straight off disk, not just when served over HTTP.
    """
    docs = ROOT / "docs"
    docs.mkdir(parents=True, exist_ok=True)

    payload = {
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "board_time": live.get("taken_at"),
        "current": live["buy"],
        "nbk": live.get("nbk"),
        "target": cfg.get("target_rate"),
        "bar": verdict.get("bar"),
        "reason": verdict.get("reason"),
        "series": [{"d": r["date"], "c": r["close"]} for r in history],
    }
    (docs / "data.json").write_text(json.dumps(payload, indent=1) + "\n")

    template = docs / "_template.html"
    if not template.exists():
        print("  ! docs/_template.html missing, page not rebuilt")
        return
    page = template.read_text().replace(
        "/*__DATA__*/null", json.dumps(payload, separators=(",", ":")), 1)
    (docs / "index.html").write_text(page)
    print(f"  page rebuilt with {len(payload['series'])} days")


# ---------- the decision ----------

def current_bar(cfg, today, prior):
    """The rate that triggers an unconditional email.

    Normally your target. If you've set a date you need the tenge by, the bar
    slides from the target down to the recent median as that date approaches —
    because holding out for a number that isn't coming has a cost too.
    """
    target = cfg["target_rate"]
    if not target:
        return None, False
    if not cfg.get("convert_by") or not prior:
        return target, False

    left = (date.fromisoformat(cfg["convert_by"]) - date.fromisoformat(today)).days
    if left >= 90:
        return target, False
    if left <= 0:
        return 0.0, True

    closes = sorted(r["close"] for r in prior)
    median = closes[len(closes) // 2]
    urgency = 1 - left / 90
    return round(target - (target - median) * urgency, 1), True


def evaluate(history, today, buy, cfg, state):
    """Decide whether this rate deserves an email.

    Two ways to qualify:
      1. It clears the bar (your target, eased if a deadline is near).
      2. It's a genuine local peak — higher than the last few weeks, clearly
         above the recent average, and not down in the year's lower half.

    Then two guards stop repeats: it must beat the last rate you were emailed
    about by a margin, unless enough time has passed that the situation is new.
    """
    prior = [r for r in history if r["date"] < today]
    stats = {"prior_days": len(prior)}

    bar, eased = current_bar(cfg, today, prior)
    stats["bar"] = bar

    if len(prior) < cfg["min_history_days"]:
        return {"send": False, "reason": None, "why_not": "not enough history yet", **stats}

    window = [r["close"] for r in prior[-cfg["peak_window_days"]:]]
    avg_src = [r["close"] for r in prior[-cfg["average_window_days"]:]]
    recent = sorted(r["close"] for r in prior[-cfg["floor_window_days"]:])

    stats["peak_to_beat"] = max(window)
    stats["average"] = round(sum(avg_src) / len(avg_src), 2)
    stats["floor"] = recent[int(len(recent) * cfg["floor_percentile"] / 100)]
    stats["vs_avg_pct"] = round(100 * (buy - stats["average"]) / stats["average"], 2)

    reason = None
    if bar is not None and buy >= bar:
        reason = "target eased by your deadline" if eased else f"reached your {bar:g} ₸ target"
    elif (buy > stats["peak_to_beat"]
          and stats["vs_avg_pct"] >= cfg["min_above_average_pct"]
          and buy >= stats["floor"]):
        reason = f"highest in {cfg['peak_window_days']} days"

    if not reason:
        return {"send": False, "reason": None, "why_not": _why_not(buy, stats, cfg), **stats}

    last_rate = state.get("last_email_rate")
    last_day = state.get("last_email_date")
    if last_rate is not None and last_day:
        gap = (date.fromisoformat(today) - date.fromisoformat(last_day)).days
        if buy < last_rate + cfg["ratchet_kzt"] and gap < cfg["repeat_after_days"]:
            return {"send": False, "reason": reason,
                    "why_not": f"already told you about {last_rate:g} ₸ {gap} days ago", **stats}

    return {"send": True, "reason": reason, "why_not": None, **stats}


def _why_not(buy, stats, cfg):
    if buy <= stats["peak_to_beat"]:
        return f"not a peak — {stats['peak_to_beat']:g} ₸ seen in the last {cfg['peak_window_days']} days"
    if stats["vs_avg_pct"] < cfg["min_above_average_pct"]:
        return f"only {stats['vs_avg_pct']:+g}% vs the {cfg['average_window_days']}-day average"
    return f"below the {cfg['floor_percentile']}th percentile of the year"


# ---------- notification ----------

def build_email(buy, live, verdict, cfg, page_url):
    reason = verdict["reason"]
    subject = f"€1 = {buy:g} ₸ — {reason}"

    def row(label, value):
        return (f'<tr><td style="padding:4px 18px 4px 0;color:#6b7f8a;">{label}</td>'
                f'<td style="padding:4px 0;font-family:ui-monospace,Menlo,monospace;">{value}</td></tr>')

    target = cfg.get("target_rate")
    rows = ""
    if target:
        gap = buy - target
        rows += row("Your target", f"{target:g} ₸ "
                    + (f"({gap:+g} ₸)" if gap >= 0 else f"({-gap:g} ₸ to go)"))
    rows += row(f"{cfg['average_window_days']}-day average", f"{verdict['average']:g} ₸ ({verdict['vs_avg_pct']:+g}%)")
    rows += row(f"Best of the last {cfg['peak_window_days']} days", f"{verdict['peak_to_beat']:g} ₸")
    if live.get("nbk"):
        rows += row("National Bank rate", f"{live['nbk']:g} ₸")
    rows += row("Board updated", (live.get("taken_at") or "—").replace("T", " ") + " Almaty time")

    example = f"{1000 * buy:,.0f}".replace(",", " ")
    html = f"""\
<div style="font-family:-apple-system,Segoe UI,sans-serif;max-width:520px;color:#16303f;">
  <p style="margin:0 0 4px;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:#6b7f8a;">
    mig.kz · Almaty · euros to tenge</p>
  <p style="margin:0;font-size:40px;font-weight:600;font-family:ui-monospace,Menlo,monospace;">{buy:g} ₸</p>
  <p style="margin:2px 0 18px;color:#6b7f8a;">per euro — €1 000 gets you about {example} ₸</p>
  <p style="margin:0 0 18px;font-size:17px;">{reason[0].upper() + reason[1:]}.</p>
  <table style="border-collapse:collapse;font-size:14px;">{rows}</table>
  <p style="margin:22px 0 0;font-size:13px;">
    <a href="{page_url}" style="color:#0f7b8a;">See the chart</a> ·
    <a href="https://mig.kz/en" style="color:#0f7b8a;">mig.kz</a>
  </p>
</div>"""

    text = (f"EUR -> KZT at mig.kz: {buy:g} tenge per euro\n{reason}\n\n"
            f"{cfg['average_window_days']}-day average: {verdict['average']:g}\n"
            f"Best of the last {cfg['peak_window_days']} days: {verdict['peak_to_beat']:g}\n\n"
            f"Chart: {page_url}\n")
    return subject, text, html


def send_email(subject, text, html):
    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or 587)
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"Tenge watch <{user}>"
    msg["To"] = os.environ.get("MAIL_TO") or user
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")

    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(user, password)
        s.send_message(msg)


# ---------- entry point ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print the verdict, change nothing")
    ap.add_argument("--force-email", action="store_true", help="send regardless (tests SMTP)")
    args = ap.parse_args()

    cfg = load_config()
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    live = mig.fetch_live()
    buy = live["buy"]

    history, today = record_today(load_history(), live)
    verdict = evaluate(history, today, buy, cfg, state)

    print(f"{today}  {buy:g} ₸  bar={verdict.get('bar')}  "
          f"peak{cfg['peak_window_days']}d={verdict.get('peak_to_beat')}  "
          f"vs avg={verdict.get('vs_avg_pct')}%")
    print("  →", verdict["reason"] if verdict["send"] else f"quiet: {verdict['why_not']}")

    if args.dry_run:
        return

    save_history(history)
    write_site_data(history, live, verdict, cfg)

    page_url = os.environ.get("PAGE_URL") or "https://mig.kz/en"
    if args.force_email or verdict["send"]:
        v = dict(verdict)
        v.setdefault("reason", None)
        if not v["reason"]:
            v["reason"] = "test email — nothing actually triggered"
        subject, text, html = build_email(buy, live, v, cfg, page_url)
        send_email(subject, text, html)
        state["last_email_rate"] = buy
        state["last_email_date"] = today
        state["last_email_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print("  emailed:", subject)

    state["last_checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["last_rate"] = buy
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


if __name__ == "__main__":
    main()
