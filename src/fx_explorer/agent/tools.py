"""The agent's tools: what the model may ask for, and the code that does it.

Two halves:

1. TOOL_DEFINITIONS: a menu we show the model. Each entry has a name, a description written
   FOR THE MODEL (when to use it, what it returns, its limits) and an input_schema (JSON
   Schema) saying which arguments exist. The model only ever sees this menu.

2. ToolBox.run(): the kitchen. When the model asks for a tool, OUR code calls OUR API over
   HTTP and returns a short, readable result. The model never touches the network or the
   database itself.

Rules (docs/agent-design.md):
  * tools call the API over HTTP, never the database;
  * every tool is one existing endpoint (tests check the schemas against openapi.yaml);
  * create_alert is NEVER executed here: it only creates a PendingAction for the human to
    confirm.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import httpx

# ------------------------------------------------------------------ which endpoint each tool uses
# (method, path as written in openapi.yaml, operationId). tests/agent/test_tool_contract.py
# checks every row against openapi.yaml, so a renamed or removed endpoint fails a test.
TOOL_ENDPOINTS: dict[str, tuple[str, str, str]] = {
    "get_pair_trend": ("GET", "/v1/pairs/{base}/{quote}", "getPair"),
    "list_rates": ("GET", "/v1/rates", "listRates"),
    "get_movers": ("GET", "/v1/movers", "listMovers"),
    "list_currencies": ("GET", "/v1/currencies", "listCurrencies"),
    "list_alerts": ("GET", "/v1/alerts", "listAlerts"),
    "create_alert": ("POST", "/v1/alerts", "createAlert"),
}

CODE_PATTERN = "^[A-Z]{3,4}$"
THRESHOLD_PATTERN = r"^(?=.*[1-9])\d+(\.\d{1,10})?$"
DATE_FORMAT = "date"

_CODE = {"type": "string", "pattern": CODE_PATTERN}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "get_pair_trend",
        "description": (
            "Summary and long-range trend of ONE currency pair over a time window. Use it for "
            "questions about how a rate changed over time (weeks to years), the latest rate, "
            "min/max/average, volatility or moving averages. The API computes all statistics "
            "exactly: quote them, never recompute them. Returns: the latest rate and date, the "
            "change vs the previous day, stats (min, max, avg), a trend block (change_abs, "
            "change_pct over the whole window, annualized_change_pct, volatility_annualized_pct, "
            "biggest_rise, biggest_fall, moving_average_30, moving_average_200) and a short "
            "sample of the rate series. The window ENDS at the latest stored date for that pair "
            "(not today). Limits: annualized_change_pct is null for windows under 365 days "
            "(use 3y, 5y or max for it). If the data starts later than the window, "
            "`data_note` says so: tell the user. Only pairs with base EUR are stored. Unknown "
            "or unstored pairs give a 404 error."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "base": {**_CODE, "description": "Base currency code, e.g. EUR."},
                "quote": {**_CODE, "description": "Quote currency code, e.g. NGN."},
                "window": {
                    "type": "string",
                    "enum": ["7d", "30d", "90d", "1y", "3y", "5y", "max"],
                    "description": "History length ending at the latest stored date. 1y = 365 "
                    "days, 3y = 1095, 5y = 1825, max = everything stored. Default 30d.",
                },
            },
            "required": ["base", "quote"],
        },
    },
    {
        "name": "list_rates",
        "description": (
            "List stored DAILY rates for one base currency, newest first. Use it for the rate "
            "on a specific date or a short date range (e.g. `from` and `to` both set to the "
            "same date gives that day's rate). Returns rows of date, quote and rate. Limits: "
            "at most 60 rows are returned to you; if `more_results_exist` or `truncated` is "
            "true, narrow the dates instead of asking for more. A range with no stored rates "
            "returns an empty list (not an error): then say the data is missing. History in "
            "this database starts around 2016-01-01. Do not use it for long-range trends: "
            "use get_pair_trend."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "base": {**_CODE, "description": "Base currency. Default EUR."},
                "quotes": {
                    "type": "array",
                    "items": _CODE,
                    "minItems": 1,
                    "maxItems": 50,
                    "description": "Quote currency codes, e.g. [\"USD\", \"GBP\"]. Omit for all.",
                },
                "from": {
                    "type": "string",
                    "format": DATE_FORMAT,
                    "description": "First date to include, YYYY-MM-DD.",
                },
                "to": {
                    "type": "string",
                    "format": DATE_FORMAT,
                    "description": "Last date to include, YYYY-MM-DD.",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Maximum rows. Default 30.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_movers",
        "description": (
            "The biggest DAY-OVER-DAY moves: for each stored currency, its latest rate vs the "
            "rate before it, biggest absolute change first. Use it for 'what moved most today / "
            "on the latest day'. Each row has its own date because currencies are not always "
            "updated on the same day. Limit: it only compares the latest two days, so it "
            "CANNOT answer questions about weeks, months or years: use get_pair_trend for "
            "those."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "base": {**_CODE, "description": "Base currency. Default EUR."},
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "description": "How many movers. Default 5.",
                },
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Only rises (up) or only falls (down). Omit for both.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "list_currencies",
        "description": (
            "Search the currency catalogue (currencies, metals, crypto). Use it to find out "
            "WHICH currencies exist or are stored (has_rates=true gives the ones with stored "
            "rates), or to turn a name ('naira') into a code ('NGN'). Returns code, name, type, "
            "rates_count (0 = no rates stored, so nothing can be answered about it) and "
            "latest_rate_date. At most 50 rows."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "q": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 100,
                    "description": "Case-insensitive search in code and name, e.g. 'naira'.",
                },
                "type": {
                    "type": "string",
                    "enum": ["currency", "metal", "crypto"],
                    "description": "Only this type.",
                },
                "has_rates": {
                    "type": "boolean",
                    "description": "true = only currencies that have stored rates.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "list_alerts",
        "description": (
            "List the user's existing rate alerts, newest first, each with the live "
            "current_rate and whether it has `triggered`. Use it when the user asks about "
            "their alerts, or to check whether a similar alert already exists before "
            "proposing a new one. Returns at most 50 alerts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["triggered", "waiting"],
                    "description": "Only triggered alerts, or only those still waiting.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "create_alert",
        "description": (
            "PROPOSE a rate alert ('tell me when EUR/USD goes above 1.20'). This does NOT "
            "create the alert: it records a pending action that the user must confirm in the "
            "app. After calling it, tell the user exactly what you propose (pair, condition, "
            "threshold, note) and ask them to confirm. Never say the alert exists until the "
            "app confirms it. Call it only when the user clearly asked for an alert; take the "
            "threshold from what the user said, never invent one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "base": {**_CODE, "description": "Base currency, e.g. EUR."},
                "quote": {**_CODE, "description": "Quote currency, e.g. USD. Must differ from base."},
                "condition": {
                    "type": "string",
                    "enum": ["above", "below"],
                    "description": "Trigger when the rate goes above or below the threshold.",
                },
                "threshold": {
                    "type": "string",
                    "pattern": THRESHOLD_PATTERN,
                    "maxLength": 40,
                    "description": "Decimal string greater than 0, at most 10 decimals, e.g. \"1.20\".",
                },
                "note": {
                    "type": ["string", "null"],
                    "maxLength": 200,
                    "description": "Optional note, at most 200 characters.",
                },
            },
            "required": ["base", "quote", "condition", "threshold"],
        },
    },
]

TOOL_NAMES = [t["name"] for t in TOOL_DEFINITIONS]
# Past this many rows we cut the list: the model would drown in it (and tokens cost money).
MAX_ROWS_TO_MODEL = 60
SERIES_SAMPLE_POINTS = 12


# ------------------------------------------------------------------ results


@dataclass
class PendingAction:
    """A write the model asked for but that has NOT happened. A human must confirm it.

    `id` doubles as the Idempotency-Key when it is finally confirmed (ADR 0006), so a
    double-click or a retry can never create two alerts.
    """

    tool: str
    input: dict[str, Any]
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "pending"  # pending | confirmed | cancelled


@dataclass
class ToolResult:
    """What one tool call produced."""

    content: str  # exactly what the model gets back (compact JSON, or an error sentence)
    source: str | None = None  # the request made, e.g. "GET /v1/pairs/EUR/NGN?window=3y"
    status: int | None = None  # HTTP status (None if no request was sent)
    is_error: bool = False
    pending_action: PendingAction | None = None


# ------------------------------------------------------------------ the executor


class ToolBox:
    """Runs tools against the API at `base_url`."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 10,
        client: httpx.Client | None = None,
    ) -> None:
        self._client = client or httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )

    def close(self) -> None:
        self._client.close()

    # -- public entry point
    def run(self, name: str, tool_input: dict[str, Any]) -> ToolResult:
        handler = {
            "get_pair_trend": self._get_pair_trend,
            "list_rates": self._list_rates,
            "get_movers": self._get_movers,
            "list_currencies": self._list_currencies,
            "list_alerts": self._list_alerts,
            "create_alert": self._create_alert,
        }.get(name)
        if handler is None:
            return _error(f"Error: unknown tool '{name}'. Available tools: {', '.join(TOOL_NAMES)}.")
        if not isinstance(tool_input, dict):
            return _error("Error: tool input must be a JSON object.")
        try:
            return handler(tool_input)
        except KeyError as missing:  # a required argument was left out
            return _error(f"Error: missing required argument {missing}.")

    # -- the five read tools
    def _get_pair_trend(self, i: dict[str, Any]) -> ToolResult:
        base, quote = _code(i["base"]), _code(i["quote"])
        return self._get(f"/v1/pairs/{base}/{quote}", {"window": i.get("window")}, _trim_pair)

    def _list_rates(self, i: dict[str, Any]) -> ToolResult:
        quotes = i.get("quotes")
        if isinstance(quotes, str):  # be forgiving: "USD,GBP" or "USD"
            quotes = [q for q in quotes.split(",") if q.strip()]
        params = {
            "base": _code(i.get("base", "EUR")),
            "quotes": ",".join(_code(q) for q in quotes) if quotes else None,
            "from": i.get("from"),
            "to": i.get("to"),
            "limit": i.get("limit", 30),
        }
        return self._get("/v1/rates", params, _trim_rates)

    def _get_movers(self, i: dict[str, Any]) -> ToolResult:
        params = {
            "base": _code(i.get("base", "EUR")),
            "limit": i.get("limit"),
            "direction": i.get("direction"),
        }
        return self._get("/v1/movers", params, _trim_movers)

    def _list_currencies(self, i: dict[str, Any]) -> ToolResult:
        has_rates = i.get("has_rates")
        params = {
            "q": i.get("q"),
            "type": i.get("type"),
            "has_rates": None if has_rates is None else str(bool(has_rates)).lower(),
        }
        return self._get("/v1/currencies", params, _trim_currencies)

    def _list_alerts(self, i: dict[str, Any]) -> ToolResult:
        return self._get("/v1/alerts", {"status": i.get("status")}, _trim_alerts)

    # -- the write tool: NO HTTP here, only a pending action
    def _create_alert(self, i: dict[str, Any]) -> ToolResult:
        problems = validate_alert_input(i)
        if problems:
            return _error("Error: invalid alert, nothing was proposed. " + " ".join(problems))
        action = PendingAction(tool="create_alert", input=normalize_alert_input(i))
        a = action.input
        text = (
            "NOT EXECUTED. No alert has been created. A pending action "
            f"(id {action.id}) now waits for the user to confirm in the app: "
            f"{a['base']}/{a['quote']} {a['condition']} {a['threshold']}"
            + (f", note \"{a['note']}\"" if a.get("note") else "")
            + ". Tell the user exactly this proposal and ask them to confirm. "
            "Do not say the alert exists."
        )
        return ToolResult(content=text, pending_action=action)

    # -- shared HTTP plumbing
    def _get(self, path: str, params: dict[str, Any], trim) -> ToolResult:
        clean = {k: v for k, v in params.items() if v is not None}
        try:
            response = self._client.get(path, params=clean)
        except httpx.TimeoutException:
            return _error(
                f"Error: the API did not answer in time ({_describe(path, clean)}).",
                source=_describe(path, clean),
            )
        except httpx.HTTPError as exc:
            return _error(
                f"Error: could not reach the API ({type(exc).__name__}).",
                source=_describe(path, clean),
            )
        source = f"GET {unquote(response.request.url.raw_path.decode())}"
        if response.status_code != 200:
            return ToolResult(
                content=_problem_text(response),
                source=source,
                status=response.status_code,
                is_error=True,
            )
        return ToolResult(
            content=json.dumps(trim(response.json()), separators=(",", ":")),
            source=source,
            status=200,
        )


# ------------------------------------------------------------------ alert input checks


def validate_alert_input(i: dict[str, Any]) -> list[str]:
    """Same rules as the contract's AlertCreate. Catching mistakes here lets the model fix
    them now, instead of the user pressing Confirm and getting a 422."""
    problems: list[str] = []
    for key in ("base", "quote", "condition", "threshold"):
        if not i.get(key):
            problems.append(f"'{key}' is required.")
    if problems:
        return problems
    base, quote = str(i["base"]).upper(), str(i["quote"]).upper()
    if not re.fullmatch(CODE_PATTERN, base) or not re.fullmatch(CODE_PATTERN, quote):
        problems.append("base and quote must be 3-4 letter currency codes.")
    elif base == quote:
        problems.append("quote must be different from base.")
    if i["condition"] not in ("above", "below"):
        problems.append("condition must be 'above' or 'below'.")
    if not re.fullmatch(THRESHOLD_PATTERN, str(i["threshold"])):
        problems.append(
            "threshold must be a plain decimal greater than 0 with at most 10 decimals (e.g. \"1.20\")."
        )
    note = i.get("note")
    if note is not None and (not isinstance(note, str) or len(note) > 200):
        problems.append("note must be text of at most 200 characters.")
    extra = set(i) - {"base", "quote", "condition", "threshold", "note"}
    if extra:
        problems.append(f"unknown fields: {', '.join(sorted(extra))}.")
    return problems


def normalize_alert_input(i: dict[str, Any]) -> dict[str, Any]:
    return {
        "base": str(i["base"]).upper(),
        "quote": str(i["quote"]).upper(),
        "condition": i["condition"],
        "threshold": str(i["threshold"]),
        "note": i.get("note"),
    }


# ------------------------------------------------------------------ helpers


def _code(value: Any) -> str:
    return str(value).strip().upper()


def _describe(path: str, params: dict[str, Any]) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"GET {path}" + (f"?{query}" if query else "")


def _error(text: str, source: str | None = None) -> ToolResult:
    return ToolResult(content=text, source=source, is_error=True)


def _problem_text(response: httpx.Response) -> str:
    """Turn an RFC 9457 Problem Details body into one short sentence the model can act on."""
    status = response.status_code
    try:
        body = response.json()
    except ValueError:
        body = None
    if status == 503:
        return "Error 503: the API or its database is unavailable right now. Try again later; do not guess."
    if not isinstance(body, dict):
        return f"Error {status}: unexpected response from the API."
    text = f"Error {status}: {body.get('detail') or body.get('title') or 'request failed'}"
    fields = body.get("errors") or []
    if fields:
        text += " " + " ".join(f"{e.get('field')}: {e.get('message')}" for e in fields)
    return text


# ------------------------------------------------------------------ result trimming
# The API answers are generous (long series, timestamps...). The model needs only the useful
# fields: smaller results are cheaper, faster, and leave less room for confusion.


def _sample(series: list[dict[str, Any]], k: int = SERIES_SAMPLE_POINTS) -> list[dict[str, Any]]:
    if len(series) <= k:
        return series
    idx = sorted({round(n * (len(series) - 1) / (k - 1)) for n in range(k)})
    return [series[n] for n in idx]


def _trim_pair(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "base": d["base"],
        "quote": d["quote"],
        "window": d["window"],
        "window_start": d["window_start"],
        "first_available_date": d["first_available_date"],
        "data_points": d["data_points"],
        "latest": d["latest"],
        "previous": d["previous"],
        # renamed so it can't be confused with trend.change_pct (the whole window):
        "latest_day_change_pct": d["change_pct"],
        "stats": d["stats"],
        "trend": d["trend"],
        "series_sample": _sample(d["series"]),
    }
    trend = d.get("trend")
    if trend and trend["period_start"] > d["window_start"]:
        out["data_note"] = (
            f"Data for this pair only starts at {trend['period_start']}, later than the "
            f"requested window start {d['window_start']}. Say so to the user."
        )
    return out


def _trim_rates(d: dict[str, Any]) -> dict[str, Any]:
    rows = [{"date": r["rate_date"], "quote": r["quote"], "rate": r["rate"]} for r in d["data"]]
    out: dict[str, Any] = {
        "base": d["data"][0]["base"] if d["data"] else None,
        "count": len(rows),
        "more_results_exist": d["page"]["next_cursor"] is not None,
    }
    if len(rows) > MAX_ROWS_TO_MODEL:
        out["truncated"] = f"showing the first {MAX_ROWS_TO_MODEL} of {len(rows)} rows; narrow the dates"
        rows = rows[:MAX_ROWS_TO_MODEL]
    out["rates"] = rows
    if not rows:
        out["note"] = "No stored rates match this request."
    return out


def _trim_movers(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "base": d["base"],
        "movers": [
            {
                "quote": m["quote"],
                "date": m["rate_date"],
                "rate": m["rate"],
                "previous_date": m["previous_rate_date"],
                "previous_rate": m["previous_rate"],
                "change_pct": m["change_pct"],
            }
            for m in d["data"]
        ],
    }


def _trim_currencies(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "count": len(d["data"]),
        "more_results_exist": d["page"]["next_cursor"] is not None,
        "currencies": [
            {
                "code": c["iso_code"],
                "name": c["name"],
                "type": c["type"],
                "rates_count": c["rates_count"],
                "latest_rate_date": c["latest_rate_date"],
            }
            for c in d["data"]
        ],
    }


def _trim_alerts(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "count": len(d["data"]),
        "more_results_exist": d["page"]["next_cursor"] is not None,
        "alerts": [
            {
                "id": a["id"],
                "pair": f"{a['base']}/{a['quote']}",
                "condition": a["condition"],
                "threshold": a["threshold"],
                "note": a["note"],
                "created_at": a["created_at"],
                "current_rate": a["current_rate"],
                "triggered": a["triggered"],
            }
            for a in d["data"]
        ],
    }
