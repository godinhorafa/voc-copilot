import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app.core.rate_limit import Rate, client_identity, parse_rate
from tests.conftest import make_settings


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30/minute", Rate(30, 60)),
        (" 5 / second ", Rate(5, 1)),
        ("1000/day", Rate(1000, 86400)),
        ("", None),
        ("0/minute", None),
    ],
)
def test_parse_rate(value: str, expected: Rate | None) -> None:
    assert parse_rate(value) == expected


@pytest.mark.parametrize("value", ["30", "30/min", "minute/30", "-1/minute"])
def test_parse_rate_rejects_bad_values(value: str) -> None:
    with pytest.raises(ValueError, match="30/minute"):
        parse_rate(value)


def test_settings_validate_rates() -> None:
    with pytest.raises(ValidationError, match="30/minute"):
        make_settings(rate_limit_ask="lots")


def _request(ip: str, key: str = "k", forwarded: str | None = None) -> Request:
    headers = [(b"x-api-key", key.encode())]
    if forwarded:
        headers.append((b"x-forwarded-for", forwarded.encode()))
    return Request({"type": "http", "headers": headers, "client": (ip, 1234)})


def test_identity_combines_key_and_ip_without_exposing_either() -> None:
    settings = make_settings()
    a = client_identity(_request("10.0.0.1"), settings)
    assert a == client_identity(_request("10.0.0.1"), settings)
    assert a != client_identity(_request("10.0.0.2"), settings)
    assert a != client_identity(_request("10.0.0.1", key="other"), settings)
    assert "10.0.0.1" not in a


def test_forwarded_for_is_used_only_when_trusted() -> None:
    proxied = _request("172.18.0.5", forwarded="203.0.113.7, 172.18.0.5")
    direct = _request("203.0.113.7")
    trusted = make_settings(rate_limit_trust_forwarded_for=True)
    untrusted = make_settings()
    assert client_identity(proxied, trusted) == client_identity(direct, trusted)
    assert client_identity(proxied, untrusted) != client_identity(direct, untrusted)
