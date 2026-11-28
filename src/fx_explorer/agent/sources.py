"""Short, human labels for the API requests an answer is based on (for source chips in the UI)."""

from urllib.parse import parse_qs, urlsplit


def label_for_request(request: str) -> str:
    """'GET /v1/pairs/EUR/NGN?window=3y' -> 'EUR/NGN trend (3y)'."""
    target = request.split(" ", 1)[-1]
    parts = urlsplit(target)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    segments = [p for p in parts.path.split("/") if p]  # ["v1", "pairs", "EUR", "NGN"]
    resource = segments[1] if len(segments) > 1 else ""

    if resource == "pairs" and len(segments) >= 4:
        return f"{segments[2]}/{segments[3]} trend ({query.get('window', '30d')})"
    if resource == "rates":
        label = f"{query.get('base', 'EUR')} rates"
        if query.get("quotes"):
            label += f" for {query['quotes']}"
        start, end = query.get("from"), query.get("to")
        if start and start == end:
            label += f" on {start}"
        elif start or end:
            label += f" {start or '…'} to {end or '…'}"
        return label
    if resource == "movers":
        return f"{query.get('base', 'EUR')} movers"
    if resource == "currencies":
        return "Currencies"
    if resource == "alerts":
        return "Your alerts"
    return target
