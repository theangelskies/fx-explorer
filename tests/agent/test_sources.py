import pytest

from fx_explorer.agent.sources import label_for_request


@pytest.mark.parametrize(
    ("request_line", "label"),
    [
        ("GET /v1/pairs/EUR/NGN?window=3y", "EUR/NGN trend (3y)"),
        ("GET /v1/pairs/EUR/USD", "EUR/USD trend (30d)"),
        ("GET /v1/rates?base=EUR&quotes=USD,GBP&from=2025-03-14&to=2025-03-14&limit=30", "EUR rates for USD,GBP on 2025-03-14"),
        ("GET /v1/rates?base=EUR&from=2025-01-01&to=2025-01-31", "EUR rates 2025-01-01 to 2025-01-31"),
        ("GET /v1/rates?base=EUR", "EUR rates"),
        ("GET /v1/movers?base=EUR", "EUR movers"),
        ("GET /v1/currencies?has_rates=true", "Currencies"),
        ("GET /v1/alerts?status=waiting", "Your alerts"),
        ("GET /v1/something-new", "/v1/something-new"),
    ],
)
def test_labels(request_line, label):
    assert label_for_request(request_line) == label
