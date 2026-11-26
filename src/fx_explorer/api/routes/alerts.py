"""Rate alerts: createAlert, listAlerts, getAlert, deleteAlert.

Validation happens in two layers, and ALL problems are reported in one 422 so a form can show
every message at once:
  1. shape: AlertCreateIn (types, enum, threshold format, note length, unknown fields)
  2. facts: currencies exist, quote != base, the pair has stored rates (needs the database)
"""

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Header, Query, Response
from pydantic import ValidationError

from fx_explorer.api.deps import alert_repository, currency_repository, rate_repository
from fx_explorer.api.errors import ProblemError, count_detail, field_error, friendly_field_errors
from fx_explorer.api.models import AlertCreateIn, AlertOut, Page, PageInfo
from fx_explorer.api.pagination import decode_cursor, encode_cursor, invalid_cursor
from fx_explorer.api.params import CODE_PATTERN
from fx_explorer.repositories.alerts import Alert, AlertRepository
from fx_explorer.repositories.currencies import CurrencyRepository
from fx_explorer.repositories.rates import RateRepository

router = APIRouter(tags=["Alerts"])

FIELD_ORDER = ["body", "base", "quote", "condition", "threshold", "note"]


def _out(a: Alert) -> AlertOut:
    return AlertOut(**vars(a))


def _validate(
    payload: Any, currencies: CurrencyRepository, rates: RateRepository
) -> AlertCreateIn:
    """Return the valid body, or raise ONE 422 listing every invalid field."""
    errors: dict[str, str] = {}
    body: AlertCreateIn | None = None
    try:
        body = AlertCreateIn.model_validate(payload)
    except ValidationError as exc:
        for e in friendly_field_errors(exc.errors(include_url=False), has_prefix=False):
            errors[e["field"]] = e["message"]

    # Fact checks, on whichever codes have a valid format (even if other fields failed).
    raw = payload if isinstance(payload, dict) else {}
    codes = {f: raw.get(f) for f in ("base", "quote")
             if f not in errors and isinstance(raw.get(f), str)
             and re.fullmatch(CODE_PATTERN, raw[f])}
    if codes:
        known = currencies.find_known(list(codes.values()))
        for field, code in codes.items():
            if code not in known:
                errors[field] = f"Unknown currency: {code}."
    base, quote = codes.get("base"), codes.get("quote")
    if base and quote and "base" not in errors and "quote" not in errors:
        if base == quote:
            errors["quote"] = "Must be different from base."
        elif not rates.has_pair(base, quote):
            errors["quote"] = (f"No rates are stored for {base}/{quote}, "
                               "so this alert could never trigger.")

    if errors:
        ordered = sorted(errors.items(), key=lambda kv: FIELD_ORDER.index(kv[0])
                         if kv[0] in FIELD_ORDER else len(FIELD_ORDER))
        raise ProblemError("validation-error", count_detail(len(ordered), "field"),
                           [field_error(f, m) for f, m in ordered])
    assert body is not None
    return body


@router.post("/v1/alerts", status_code=201, response_model=AlertOut)
def create_alert(
    response: Response,
    payload: Any = Body(...),
    # Accepted and length-checked now (contract); the replay behaviour is ADR 0006 / Phase 10.
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", min_length=1, max_length=255),
    alerts: AlertRepository = Depends(alert_repository),
    currencies: CurrencyRepository = Depends(currency_repository),
    rates: RateRepository = Depends(rate_repository),
) -> AlertOut:
    body = _validate(payload, currencies, rates)
    alert = alerts.create(base=body.base, quote=body.quote, condition=body.condition,
                          threshold=body.threshold, note=body.note)
    response.headers["Location"] = f"/v1/alerts/{alert.id}"
    return _out(alert)


def _decode_alerts_cursor(cursor: str) -> tuple[datetime, UUID]:
    values = decode_cursor(cursor, {"t", "i"})
    try:
        ts = datetime.fromisoformat(values["t"])
        if ts.tzinfo is None:
            raise ValueError("naive timestamp")
        return ts, UUID(values["i"])
    except ValueError:
        raise invalid_cursor() from None


@router.get("/v1/alerts", response_model=Page[AlertOut])
def list_alerts(
    status: Literal["triggered", "waiting"] | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(None, min_length=1, max_length=512),
    alerts: AlertRepository = Depends(alert_repository),
) -> Page[AlertOut]:
    after = _decode_alerts_cursor(cursor) if cursor is not None else None
    triggered = None if status is None else status == "triggered"
    page = alerts.list_page(limit=limit, triggered=triggered, after=after)
    next_cursor = None
    if page.has_more:
        last = page.items[-1]
        # isoformat keeps microseconds and the UTC offset, so the position is exact.
        next_cursor = encode_cursor({"t": last.created_at.isoformat(), "i": str(last.id)})
    return Page[AlertOut](data=[_out(a) for a in page.items],
                          page=PageInfo(limit=limit, next_cursor=next_cursor))


@router.get("/v1/alerts/{id}", response_model=AlertOut)
def get_alert(id: UUID, alerts: AlertRepository = Depends(alert_repository)) -> AlertOut:
    alert = alerts.get(id)
    if alert is None:
        raise ProblemError("not-found", f"No alert with id '{id}'.")
    return _out(alert)


@router.delete("/v1/alerts/{id}", status_code=204)
def delete_alert(id: UUID, alerts: AlertRepository = Depends(alert_repository)) -> Response:
    if not alerts.delete(id):
        raise ProblemError("not-found", f"No alert with id '{id}'.")
    return Response(status_code=204)
