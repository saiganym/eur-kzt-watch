#!/usr/bin/env python3
"""Replay the real notification rules over stored history.

  python3 scripts/backtest.py              # the whole file
  python3 scripts/backtest.py --from 2026-03-10

Uses the same evaluate() the live checker uses, so what you see here is what
you'd have received. Change config.json and re-run to feel the difference.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from check_rate import evaluate, load_config, load_history  # noqa: E402


def replay(history, cfg, start=None):
    sent, state = [], {}
    for r in history:
        if start and r["date"] < start:
            continue
        v = evaluate(history, r["date"], r["close"], cfg, state)
        if v["send"]:
            sent.append((r["date"], r["close"], v["reason"]))
            state = {"last_email_rate": r["close"], "last_email_date": r["date"]}
    return sent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", help="only replay from this date (YYYY-MM-DD)")
    args = ap.parse_args()

    cfg = load_config()
    history = load_history()
    window = [r for r in history if not args.start or r["date"] >= args.start]
    sent = replay(history, cfg, args.start)

    lo = min(r["close"] for r in window)
    hi = max(r["close"] for r in window)
    print(f"{window[0]['date']} → {window[-1]['date']}  ·  {len(window)} days  ·  {lo:g}–{hi:g} ₸\n")

    for d, c, why in sent:
        print(f"  {d}   {c:>6g} ₸   {why}")

    if not sent:
        print("  no emails")
        return
    best = max(c for _, c, _ in sent)
    print(f"\n{len(sent)} emails — one every {len(window) // len(sent)} days")
    print(f"best rate flagged: {best:g} ₸   ·   actual peak: {hi:g} ₸   ·   missed by {hi - best:g} ₸")


if __name__ == "__main__":
    main()
