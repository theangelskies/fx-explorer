"""Compute the correct answers for evals/questions.yaml straight from the database.

    uv run python evals/build_expected.py

Why: ground truth must come from DATA, not from memory (or from the agent, or from our API).
So this script runs its own SQL against the database. It deliberately does NOT call the API
or reuse analytics/trends.py: if the API had a bug, the eval should catch it, not copy it.

Output: evals/expected.json  {question id: {field: value, ...}}. Numbers are strings
(same reason as the API: exact decimals). Re-run it whenever new rates are imported, because
"latest" answers move. The file records when it was built and the newest stored date.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from decimal import Decimal
from pathlib import Path

import psycopg
import yaml

from fx_explorer.config import get_settings

HERE = Path(__file__).parent
WINDOW_DAYS = {"7d": 7, "30d": 30, "90d": 90, "1y": 365, "3y": 1095, "5y": 1825, "max": None}
Q2 = Decimal("0.01")


def fmt(value: object, places: str | None = None) -> str | None:
    """Decimal -> plain string (no exponent). None stays None."""
    if value is None:
        return None
    d = Decimal(value)
    if places is not None:
        d = d.quantize(Decimal(places))
    return format(d.normalize() if places is None else d, "f")


# ----------------------------------------------------------------------------- SQL helpers


def rate_on_date(conn, base: str, quote: str, date: str) -> dict:
    row = conn.execute(
        "SELECT rate_date, rate FROM exchange_rates WHERE base=%s AND quote=%s AND rate_date=%s",
        (base, quote, date),
    ).fetchone()
    if row is None:
        raise SystemExit(f"No stored rate for {base}/{quote} on {date}: pick another date in questions.yaml")
    return {"date": row[0].isoformat(), "rate": fmt(row[1])}


def latest_rate(conn, base: str, quote: str) -> dict:
    row = conn.execute(
        "SELECT rate_date, rate FROM exchange_rates WHERE base=%s AND quote=%s "
        "ORDER BY rate_date DESC LIMIT 1",
        (base, quote),
    ).fetchone()
    if row is None:
        raise SystemExit(f"No rates stored for {base}/{quote}")
    return {"date": row[0].isoformat(), "rate": fmt(row[1])}


WINDOW_SQL = """
WITH last AS (
    SELECT max(rate_date) AS d FROM exchange_rates WHERE base = %(b)s AND quote = %(q)s
),
w AS (                       -- the window ENDS at (and counts) the latest stored date
    SELECT rate_date, rate FROM exchange_rates, last
    WHERE base = %(b)s AND quote = %(q)s
      AND (%(days)s::int IS NULL OR rate_date > last.d - %(days)s::int)
),
f AS (SELECT rate_date, rate FROM w ORDER BY rate_date ASC  LIMIT 1),
l AS (SELECT rate_date, rate FROM w ORDER BY rate_date DESC LIMIT 1),
rets AS (SELECT ln(rate / lag(rate) OVER (ORDER BY rate_date)) AS r FROM w)
SELECT f.rate_date, f.rate, l.rate_date, l.rate,
       (SELECT min(rate) FROM w), (SELECT max(rate) FROM w), (SELECT avg(rate) FROM w),
       (SELECT count(*) FROM w),
       (SELECT count(r) FROM rets), (SELECT stddev_samp(r) FROM rets)
FROM f, l
"""


def window_trend(conn, base: str, quote: str, window: str) -> dict:
    row = conn.execute(
        WINDOW_SQL, {"b": base, "q": quote, "days": WINDOW_DAYS[window]}
    ).fetchone()
    if row is None:
        raise SystemExit(f"No rates stored for {base}/{quote}")
    f_date, f_rate, l_date, l_rate, mn, mx, avg, n, n_ret, sd = row
    span = (l_date - f_date).days
    change_pct = (l_rate / f_rate - 1) * 100
    # Same definitions as ADR 0005, written independently here:
    annualized = None
    if span >= 365:
        annualized = ((l_rate / f_rate) ** (Decimal("365.25") / span) - 1) * 100
    volatility = None
    if n_ret >= 20 and sd is not None:       # ADR 0005: needs at least 20 daily returns
        volatility = sd * Decimal(365).sqrt() * 100
    return {
        "window": window,
        "first_date": f_date.isoformat(), "first_rate": fmt(f_rate),
        "last_date": l_date.isoformat(), "last_rate": fmt(l_rate),
        "span_days": span, "n_rates": n,
        "change_abs": fmt(l_rate - f_rate),
        "change_pct": fmt(change_pct, "0.01"),
        "annualized_change_pct": fmt(annualized, "0.01"),
        "volatility_annualized_pct": fmt(volatility, "0.01"),
        "min": fmt(mn), "max": fmt(mx), "avg": fmt(avg, "0.0001"),
    }


def stored_quotes(conn, base: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT quote FROM exchange_rates WHERE base=%s ORDER BY quote", (base,))]


def top_mover(conn, base: str) -> dict:
    """Latest stored rate vs the one before it, per quote; biggest absolute move first."""
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT quote, rate_date, rate,
                   row_number() OVER (PARTITION BY quote ORDER BY rate_date DESC) AS rn
            FROM exchange_rates WHERE base = %s
        )
        SELECT a.quote, a.rate_date, a.rate, b.rate_date, b.rate,
               (a.rate / b.rate - 1) * 100 AS pct
        FROM ranked a JOIN ranked b ON a.quote = b.quote AND a.rn = 1 AND b.rn = 2
        ORDER BY abs((a.rate / b.rate - 1)) DESC, a.quote
        """,
        (base,),
    ).fetchall()
    ranking = [
        {"quote": q, "date": d.isoformat(), "rate": fmt(r), "previous_date": pd.isoformat(),
         "previous_rate": fmt(pr), "change_pct": fmt(pct, "0.01")}
        for q, d, r, pd, pr, pct in rows
    ]
    return {**ranking[0], "ranking": ranking}


# ----------------------------------------------------------------------------- one question


def expected_for(conn, truth: dict) -> dict:
    kind = truth["kind"]
    if kind == "none":
        return {}
    if kind == "rate_on_date":
        return rate_on_date(conn, truth["base"], truth["quote"], truth["date"])
    if kind == "latest_rate":
        return latest_rate(conn, truth["base"], truth["quote"])
    if kind == "window_trend":
        return window_trend(conn, truth["base"], truth["quote"], truth["window"])
    if kind == "biggest_window_move":
        per_quote = {q: window_trend(conn, truth["base"], q, truth["window"])
                     for q in stored_quotes(conn, truth["base"])}
        best = max(per_quote, key=lambda q: abs(Decimal(per_quote[q]["change_pct"])))
        return {"quote": best, "change_pct": per_quote[best]["change_pct"],
                "window": truth["window"],
                "all": {q: v["change_pct"] for q, v in per_quote.items()}}
    if kind == "compare_window":
        out: dict = {"window": truth["window"]}
        for q in truth["quotes"]:
            out[f"{q}_change_pct"] = window_trend(conn, truth["base"], q, truth["window"])["change_pct"]
        # "gained more" = bigger change_pct (the base rose more against that quote)
        out["winner"] = max(truth["quotes"], key=lambda q: Decimal(out[f"{q}_change_pct"]))
        return out
    if kind == "top_mover":
        return top_mover(conn, truth["base"])
    if kind == "conversion":
        latest = latest_rate(conn, truth["base"], truth["quote"])
        amount = Decimal(truth["amount"])
        return {**latest, "amount": truth["amount"],
                "result": fmt(amount * Decimal(latest["rate"]), "0.01")}
    if kind == "before_first_date":
        first = conn.execute(
            "SELECT min(rate_date) FROM exchange_rates WHERE base=%s AND quote=%s",
            (truth["base"], truth["quote"]),
        ).fetchone()[0]
        asked = dt.date.fromisoformat(truth["date"])
        if first is None or asked >= first:
            raise SystemExit(f"{truth['date']} is not before the first stored date ({first})")
        return {"asked_date": truth["date"], "first_stored_date": first.isoformat(),
                "rows_on_or_before_asked_date": 0}
    if kind == "no_rates_stored":
        n = conn.execute(
            "SELECT count(*) FROM exchange_rates WHERE base=%s AND quote=%s",
            (truth["base"], truth["quote"]),
        ).fetchone()[0]
        known = conn.execute(
            "SELECT count(*) FROM currencies WHERE iso_code=%s", (truth["quote"],)
        ).fetchone()[0]
        if n:
            raise SystemExit(f"{truth['quote']} has {n} stored rates: pick a currency without any")
        return {"rates_stored": 0, "currency_is_known": bool(known)}
    raise SystemExit(f"Unknown truth kind: {kind}")


def main(quiet: bool = False) -> None:
    questions = yaml.safe_load((HERE / "questions.yaml").read_text())
    ids = [q["id"] for q in questions]
    if len(ids) != len(set(ids)):
        raise SystemExit("Duplicate question ids")

    expected: dict = {}
    with psycopg.connect(get_settings().database_url) as conn:
        newest = conn.execute("SELECT max(rate_date) FROM exchange_rates").fetchone()[0]
        for q in questions:
            expected[q["id"]] = expected_for(conn, q["truth"])

    out = {
        "_meta": {
            "built_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "newest_stored_date": newest.isoformat(),
            "note": "Computed by evals/build_expected.py from the database. Do not edit by hand.",
        },
        **expected,
    }
    (HERE / "expected.json").write_text(json.dumps(out, indent=2) + "\n")

    if quiet:
        return
    print(f"Wrote evals/expected.json ({len(questions)} questions, data up to {newest})\n")
    for q in questions:
        e = {k: v for k, v in expected[q["id"]].items() if k not in ("ranking", "all")}
        print(f"[{q['id']}] {q['question']}")
        print(f"    -> {json.dumps(e) if e else '(no numbers; judged on behaviour)'}")
        must = [m if isinstance(m, str) else next(iter(m)) for m in q["judge"].get("must", [])]
        print(f"    must: {', '.join(must)}")


if __name__ == "__main__":
    sys.exit(main())
