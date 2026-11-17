"""PII redaction applied to transcripts before they are stored, embedded or
sent to an LLM.

Detectors are deterministic: a pattern finds candidates and a validator
rejects look-alikes (Luhn for card numbers, mod-97 for IBANs, check digits
for CPF, octet ranges for IPv4), so prices, plan codes and error codes in
customer service text are left alone. Person names and street addresses need
NER and are NOT detected here; `Redactor` takes any detectors, so an NER-based
one (e.g. Presidio) can be added without touching callers.

Long digit runs that no specific detector accepts (a mistyped CPF, a "card"
failing Luhn) are still redacted as NUMBER: a mistyped ID is usually the real
one with a typo, so the redactor errs on the side of privacy.

Placeholders are typed and stable within one redaction call: the same email
in two turns of a conversation becomes `[EMAIL_1]` both times, so the text
stays coherent for retrieval and extraction.
"""

import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol


class Detector(Protocol):
    @property
    def kind(self) -> str: ...

    def find(self, text: str) -> Iterable[tuple[int, int]]:
        """Spans (start, end) of this kind of PII in `text`."""
        ...


@dataclass(frozen=True)
class PatternDetector:
    kind: str
    pattern: re.Pattern[str]
    validate: Callable[[str], bool] = lambda _: True

    def find(self, text: str) -> Iterable[tuple[int, int]]:
        for match in self.pattern.finditer(text):
            if self.validate(match.group()):
                yield match.span()


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def luhn_valid(value: str) -> bool:
    digits = _digits(value)
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        n = int(d)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def iban_valid(value: str) -> bool:
    compact = value.replace(" ", "").upper()
    if not 15 <= len(compact) <= 34:
        return False
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(c, 36)) for c in rearranged)
    return int(numeric) % 97 == 1


def cpf_valid(value: str) -> bool:
    digits = [int(d) for d in _digits(value)]
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for length in (9, 10):
        total = sum(d * w for d, w in zip(digits[:length], range(length + 1, 1, -1), strict=True))
        check = (total * 10) % 11 % 10
        if digits[length] != check:
            return False
    return True


def ssn_valid(value: str) -> bool:
    area, group, serial = value.split("-")
    return (
        area not in {"000", "666"}
        and not area.startswith("9")
        and group != "00"
        and serial != "0000"
    )


def phone_valid(value: str) -> bool:
    # 9-15 digits (E.164 max is 15); fewer is more likely a price, date or code.
    return 9 <= len(_digits(value)) <= 15


def ipv4_valid(value: str) -> bool:
    return all(0 <= int(octet) <= 255 for octet in value.split("."))


EMAIL = PatternDetector("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"))
CARD = PatternDetector(
    "CARD", re.compile(r"(?<![\d.$])(?:\d[ -]?){12,18}\d(?!\d|\.\d)"), luhn_valid
)
IBAN = PatternDetector(
    "IBAN", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b"), iban_valid
)
CPF = PatternDetector("CPF", re.compile(r"(?<!\d)\d{3}\.\d{3}\.\d{3}-\d{2}(?!\d)"), cpf_valid)
SSN = PatternDetector("SSN", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"), ssn_valid)
# Digits in groups separated by space, dot, dash or parentheses, optionally with
# a +country code. Not preceded by $ or a letter-dash code (e.g. ERR-4G-503); a
# sentence-ending period may follow, a decimal or version part may not.
_DIGIT_GROUPS = re.compile(
    r"(?<![\w$.-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{1,4}\)[\s.-]?)?"
    r"\d{2,5}(?:[\s.-]\d{2,5}){1,5}(?![\w-]|\.\d)"
)
PHONE = PatternDetector("PHONE", _DIGIT_GROUPS, phone_valid)
# Catch-all, last: any long digit run the specific detectors rejected.
NUMBER = PatternDetector("NUMBER", _DIGIT_GROUPS, lambda value: 9 <= len(_digits(value)) <= 19)
IPV4 = PatternDetector(
    "IP", re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)"), ipv4_valid
)

# Order matters when spans overlap: earlier detectors win (a card number is
# also a long digit run that the phone pattern would match).
DEFAULT_DETECTORS: tuple[Detector, ...] = (EMAIL, IBAN, CARD, CPF, SSN, IPV4, PHONE, NUMBER)


@dataclass
class Redaction:
    texts: list[str]
    counts: dict[str, int] = field(default_factory=dict)


class Redactor:
    def __init__(self, detectors: Sequence[Detector] = DEFAULT_DETECTORS) -> None:
        self._detectors = detectors

    def _spans(self, text: str) -> list[tuple[int, int, str]]:
        taken: list[tuple[int, int, str]] = []
        for detector in self._detectors:
            for start, end in detector.find(text):
                if all(end <= s or start >= e for s, e, _ in taken):
                    taken.append((start, end, detector.kind))
        return sorted(taken)

    def redact(self, texts: Sequence[str]) -> Redaction:
        """Redact several texts that belong together (e.g. one conversation's turns)."""
        placeholders: dict[tuple[str, str], str] = {}
        per_kind: Counter[str] = Counter()
        counts: Counter[str] = Counter()
        out = []
        for text in texts:
            pieces, last = [], 0
            for start, end, kind in self._spans(text):
                value = text[start:end]
                key = (kind, _digits(value) or value.lower())
                if key not in placeholders:
                    per_kind[kind] += 1
                    placeholders[key] = f"[{kind}_{per_kind[kind]}]"
                pieces += [text[last:start], placeholders[key]]
                counts[kind] += 1
                last = end
            pieces.append(text[last:])
            out.append("".join(pieces))
        return Redaction(texts=out, counts=dict(counts))
