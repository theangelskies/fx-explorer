"""HTTP client for the Frankfurter API v2.

This module only talks HTTP. It never touches the database and it does not
validate the data: it returns the raw records (as dicts) for the parser to check.
"""

import json
import logging
import random
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from fx_explorer.config import Settings

logger = logging.getLogger(__name__)

# HTTP status codes that are worth trying again: "slow down" and "server problem".
RETRYABLE_STATUS = {429} | set(range(500, 600))

# Never sleep longer than this because of a Retry-After header (safety net).
MAX_RETRY_AFTER_SECONDS = 60.0


class FrankfurterError(Exception):
    """Base class for everything this client raises."""


class ApiError(FrankfurterError):
    """The API answered with an error that retrying will not fix (400, 404, 422, ...)."""

    def __init__(self, status_code: int, message: str, url: str) -> None:
        self.status_code = status_code
        self.message = message
        self.url = url
        super().__init__(f"Frankfurter API error {status_code} for {url}: {message}")


class RetriesExhaustedError(FrankfurterError):
    """Every attempt failed with a temporary problem (network, timeout, 429 or 5xx)."""


class FrankfurterClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 10,
        max_retries: int = 3,
        request_delay_seconds: float = 1,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = httpx.Client(base_url=base_url, timeout=timeout_seconds)
        self._max_retries = max_retries
        self._request_delay = request_delay_seconds
        self._sleep = sleep  # replaced by a fake in tests so they run instantly
        self._lock = threading.Lock()  # only one request at a time
        self._last_request_finished: float | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "FrankfurterClient":
        return cls(
            base_url=settings.frankfurter_base_url,
            timeout_seconds=settings.http_timeout_seconds,
            max_retries=settings.max_retries,
            request_delay_seconds=settings.request_delay_seconds,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "FrankfurterClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------ public API

    def get_currencies(self) -> list[dict[str, Any]]:
        """GET /currencies: the list of currencies (raw records)."""
        return self._get_json("/currencies")

    def get_rates(
        self,
        base: str,
        quotes: Sequence[str],
        date: date | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> list[dict[str, Any]]:
        """GET /rates for one `date`, or a `start`..`end` range, or the latest if none given.

        The API does not allow a single date together with a range, so we refuse it here.
        """
        if date is not None and (start is not None or end is not None):
            raise ValueError("Use either `date` or `start`/`end`, not both.")

        params: dict[str, str] = {"base": base, "quotes": ",".join(quotes)}
        if date is not None:
            params["date"] = date.isoformat()
        if start is not None:
            params["from"] = start.isoformat()
        if end is not None:
            params["to"] = end.isoformat()
        return self._get_json("/rates", params)

    # ------------------------------------------------------------------ internals

    def _get_json(self, path: str, params: dict[str, str] | None = None) -> Any:
        with self._lock:
            response = self._request_with_retries(path, params)
        try:
            # parse_float=Decimal keeps 1512.17 exact instead of turning it into a float.
            return json.loads(response.content, parse_float=Decimal)
        except ValueError as exc:
            raise FrankfurterError(f"Response from {response.url} was not valid JSON") from exc

    def _request_with_retries(self, path: str, params: dict[str, str] | None) -> httpx.Response:
        last_problem = "unknown"
        for attempt in range(self._max_retries + 1):
            retry_after: float | None = None
            self._throttle()
            try:
                response = self._http.get(path, params=params)
            except httpx.TransportError as exc:  # network errors and timeouts
                last_problem = f"{type(exc).__name__}: {exc}"
            else:
                if response.is_success:
                    return response
                if response.status_code not in RETRYABLE_STATUS:
                    raise ApiError(
                        response.status_code, _error_message(response), str(response.url)
                    )
                last_problem = f"HTTP {response.status_code}"
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            finally:
                self._last_request_finished = time.monotonic()

            if attempt == self._max_retries:
                break
            wait = retry_after if retry_after is not None else _backoff_seconds(attempt)
            logger.warning(
                "Request to %s failed (%s). Retry %d/%d in %.1fs",
                path, last_problem, attempt + 1, self._max_retries, wait,
            )
            self._sleep(wait)

        raise RetriesExhaustedError(
            f"GET {path} failed after {self._max_retries + 1} attempts. Last problem: {last_problem}"
        )

    def _throttle(self) -> None:
        """Wait so that at least request_delay seconds pass between two requests."""
        if self._last_request_finished is None or self._request_delay <= 0:
            return
        wait = self._request_delay - (time.monotonic() - self._last_request_finished)
        if wait > 0:
            self._sleep(wait)


def _backoff_seconds(attempt: int) -> float:
    """1s, 2s, 4s, ... plus up to 25% random jitter so clients don't all retry together."""
    base = 2.0**attempt
    return base + random.uniform(0, base * 0.25)


def _parse_retry_after(value: str | None) -> float | None:
    """Retry-After is either a number of seconds or an HTTP date. Returns seconds to wait."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - datetime.now(UTC)).total_seconds()
    return min(max(seconds, 0.0), MAX_RETRY_AFTER_SECONDS)


def _error_message(response: httpx.Response) -> str:
    """The API's error body looks like {"status": 422, "message": "..."}."""
    try:
        body = response.json()
        if isinstance(body, dict) and "message" in body:
            return str(body["message"])
    except ValueError:
        pass
    return response.text[:200] or response.reason_phrase
