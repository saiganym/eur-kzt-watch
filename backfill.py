#!/usr/bin/env python3
"""Seed data/rates.csv with real history from mig.kz's rate archive.

  python3 scripts/backfill.py --months 12

Run once at setup so the chart and the "is this a good rate" logic have
something to work with on day one. Safe to re-run: existing days are updated,
nothing is duplicated.
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import mig  # noqa: E402
from check_rate import load_history, save_history  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=12, help="how far back to go")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=31 * args.months)

    merged = {r["date"]: r for r in load_history()}
    added = 0

    # The archive returns ~1000 intraday quotes per page, so walk it in
    # month-sized chunks rather than asking for a year in one go.
    chunk_start = start
    while chunk_start < end:
        chunk_end = min(chunk_start + timedelta(days=31), end)
        quotes = mig.fetch_archive(chunk_start, chunk_end)
        days = mig.to_daily(quotes)
        print(f"{chunk_start} → {chunk_end}: {len(quotes)} quotes, {len(days)} days")
        for d in days:
            if d["date"] not in merged:
                added += 1
            merged[d["date"]] = {**merged.get(d["date"], {}), **d}
        chunk_start = chunk_end + timedelta(days=1)

    rows = sorted(merged.values(), key=lambda r: r["date"])
    save_history(rows)

    closes = [r["close"] for r in rows]
    print(f"\nSaved {len(rows)} days ({added} new) to data/rates.csv")
    if closes:
        print(f"Range {rows[0]['date']} → {rows[-1]['date']}: "
              f"low {min(closes):g} ₸, high {max(closes):g} ₸, latest {closes[-1]:g} ₸")


if __name__ == "__main__":
    main()
