"""Fetch EUR/KZT rates from mig.kz (MiG exchange offices, Almaty).

Terminology, from the perspective of someone holding euros:
  BUY  = the rate at which MiG buys your EUR. This is what you get. Higher is better.
  SELL = the rate at which MiG sells you EUR. Not relevant when converting EUR -> KZT.
"""

import re
import urllib.parse
import urllib.request
from datetime import datetime

BASE = "https://mig.kz"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

RU_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


def _get(url, data=None, timeout=45):
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={"User-Agent": UA, "X-Requested-With": "XMLHttpRequest"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_live():
    """Current MiG board + the National Bank reference rate.

    Returns dict: taken_at (ISO, Almaty local), buy, sell, nbk.
    """
    html = _get(f"{BASE}/en")

    row = re.search(
        r'<td class="buy[^"]*">\s*([\d.]+)\s*</td>\s*'
        r'<td class="currency">EUR</td>\s*'
        r'<td class="sell[^"]*">\s*([\d.]+)\s*</td>',
        html, re.S,
    )
    if not row:
        raise RuntimeError("EUR row not found on mig.kz — page layout may have changed")

    stamp = re.search(r"on\s+([A-Z][a-z]+ \d{1,2} \d{4} \d{1,2}:\d{2})", html)
    taken_at = None
    if stamp:
        try:
            taken_at = datetime.strptime(stamp.group(1), "%B %d %Y %H:%M").isoformat(timespec="minutes")
        except ValueError:
            pass

    nbk = re.search(r"<h4>EUR</h4>\s*<p>([\d.]+) tenge</p>", html)

    return {
        "taken_at": taken_at,
        "buy": float(row.group(1)),
        "sell": float(row.group(2)),
        "nbk": float(nbk.group(1)) if nbk else None,
    }


def fetch_archive(date_from, date_to, max_pages=12):
    """Historical quotes from MiG's archive search endpoint.

    date_from / date_to are `datetime.date`. Returns a list of
    (datetime, buy, sell) tuples, newest first, including intraday changes.
    """
    out, seen = [], set()

    for page in range(1, max_pages + 1):
        html = _get(
            f"{BASE}/archive/search",
            {
                "page": page,
                "from[day]": date_from.day,
                "from[month]": date_from.month,
                "from[year]": date_from.year,
                "to[day]": date_to.day,
                "to[month]": date_to.month,
                "to[year]": date_to.year,
            },
        )

        rows = re.findall(
            r'<td class="date-label">\s*(.*?)\s*</td>(.*?)</tr>', html, re.S
        )
        if not rows:
            break

        new = 0
        for label, cells in rows:
            ts = _parse_ru_date(label)
            if not ts:
                continue
            buy = re.search(r'EUR-buy[^>]*>\s*([\d.]+)', cells)
            sell = re.search(r'EUR-sell[^>]*>\s*([\d.]+)', cells)
            if not buy:
                continue
            key = ts.isoformat()
            if key in seen:
                continue
            seen.add(key)
            new += 1
            out.append((ts, float(buy.group(1)), float(sell.group(1)) if sell else None))

        if new == 0:
            break

    out.sort(key=lambda r: r[0], reverse=True)
    return out


def _parse_ru_date(label):
    m = re.match(r"(\d{1,2})\s+([а-яё]+)\s+(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", label.strip(), re.I)
    if not m:
        return None
    day, month_name, year, hh, mm = m.groups()
    month = RU_MONTHS.get(month_name.lower())
    if not month:
        return None
    return datetime(int(year), month, int(day), int(hh or 0), int(mm or 0))


def to_daily(quotes):
    """Collapse intraday quotes into one record per day.

    close = last quote of the day (what the board showed at end of day)
    high  = best buy rate seen that day
    """
    days = {}
    for ts, buy, sell in sorted(quotes, key=lambda r: r[0]):
        d = ts.date().isoformat()
        rec = days.setdefault(d, {"date": d, "close": buy, "high": buy, "low": buy, "sell": sell})
        rec["close"] = buy
        rec["sell"] = sell
        rec["high"] = max(rec["high"], buy)
        rec["low"] = min(rec["low"], buy)
    return [days[k] for k in sorted(days)]
