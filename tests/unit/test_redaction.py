import json
from pathlib import Path

import pytest

from app.privacy.redaction import (
    Redactor,
    cpf_valid,
    iban_valid,
    luhn_valid,
)

REDACTOR = Redactor()


def redact(text: str) -> str:
    return REDACTOR.redact([text]).texts[0]


# (text, expected) for PII that must be caught
POSITIVES = [
    ("Mail jane.doe+bills@example.co.uk now", "Mail [EMAIL_1] now"),
    ("Call +1 (415) 555-0132.", "Call [PHONE_1]."),
    ("Ligue 11 98765-4321 amanhã", "Ligue [PHONE_1] amanhã"),
    ("Freephone 0800 123 4567", "Freephone [PHONE_1]"),
    ("Card 4111 1111 1111 1111 declined", "Card [CARD_1] declined"),
    ("Card 4111-1111-1111-1111.", "Card [CARD_1]."),
    ("IBAN DE89 3704 0044 0532 0130 00 ok", "IBAN [IBAN_1] ok"),
    ("IBAN GB82WEST12345698765432", "IBAN [IBAN_1]"),
    ("CPF 529.982.247-25", "CPF [CPF_1]"),
    ("SSN 123-45-6789", "SSN [SSN_1]"),
    ("From 192.168.1.20.", "From [IP_1]."),
]

# Text that looks numeric or code-like but is not PII and must survive intact
NEGATIVES = [
    "My bill went up from $49.90 to $57.38",
    "I paid $1,234.56 and was refunded 120.00",
    "Plan LM-PLUS-50 shows ERR-4G-503 and AUTH-401-MIGR",
    "Outage from 2026-10-14 to 2026-10-16, ordered 03/09/2026 at 10:30",
    "Version 1.2.3.4.5, build v2.10.3",
    "Ticket #48213, order 7781234",
    "It will take 3-5 business days",
    "Speeds of 500 Mbps on 2.4 GHz, 5 GHz and 6 GHz",
    "Account ending in 4321",
]


# Long digit runs that fail a format's validation (a mistyped CPF, an invalid
# SSN area, a "card" failing Luhn) are still caught by the NUMBER catch-all:
# a mistyped ID is usually the customer's real ID with a typo, so
# over-redacting is the safe side.
# 9-15 digits also fit a phone number, so those are labeled PHONE.
NEAR_MISSES = [
    ("4111 1111 1111 1112", "NUMBER"),
    ("111.111.111-11", "PHONE"),
    ("000-12-3456", "PHONE"),
]


@pytest.mark.parametrize(("text", "expected"), POSITIVES)
def test_detects_pii(text: str, expected: str) -> None:
    assert redact(text) == expected


@pytest.mark.parametrize("text", NEGATIVES)
def test_leaves_look_alikes_alone(text: str) -> None:
    assert redact(text) == text


@pytest.mark.parametrize(("text", "kind"), NEAR_MISSES)
def test_invalid_ids_are_still_redacted(text: str, kind: str) -> None:
    assert redact(f"Mine is {text} I think") == f"Mine is [{kind}_1] I think"


def test_placeholders_are_stable_across_turns() -> None:
    result = REDACTOR.redact(
        [
            "My email is ana@example.com",
            "Sorry, can you repeat?",
            "ANA@example.com, and the other one is bo@example.com",
        ]
    )
    assert result.texts == [
        "My email is [EMAIL_1]",
        "Sorry, can you repeat?",
        "[EMAIL_1], and the other one is [EMAIL_2]",
    ]
    assert result.counts == {"EMAIL": 3}


def test_same_number_formatted_differently_keeps_its_placeholder() -> None:
    result = REDACTOR.redact(["4111 1111 1111 1111", "4111-1111-1111-1111"])
    assert result.texts == ["[CARD_1]", "[CARD_1]"]


def test_card_is_not_also_a_phone() -> None:
    assert redact("4111 1111 1111 1111") == "[CARD_1]"


@pytest.mark.parametrize(
    ("validator", "good", "bad"),
    [
        (luhn_valid, "4012888888881881", "4012888888881882"),
        (iban_valid, "FR1420041010050500013M02606", "FR1420041010050500013M02607"),
        (cpf_valid, "52998224725", "52998224724"),
    ],
)
def test_validators(validator: object, good: str, bad: str) -> None:
    assert validator(good) is True  # type: ignore[operator]
    assert validator(bad) is False  # type: ignore[operator]


def test_no_false_positives_on_the_synthetic_dataset() -> None:
    """The committed dataset has no PII; any redaction there is a false positive."""
    path = Path(__file__).parents[2] / "synthetic" / "datasets" / "telecom-seed42-n500"
    for line in (path / "conversations.jsonl").read_text().splitlines():
        turns = [t["text"] for t in json.loads(line)["turns"]]
        assert REDACTOR.redact(turns).counts == {}, turns
