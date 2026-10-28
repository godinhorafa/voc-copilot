"""Citation validation: every citation must point at a retrieved source and
quote text that actually appears in it."""

import re
from dataclasses import dataclass

from app.retrieval.types import RetrievedChunk

_SPACE = re.compile(r"\s+")
# Typographic quotes the model may emit, mapped to plain ones.
_QUOTES = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})


def normalize(text: str) -> str:
    return _SPACE.sub(" ", text.translate(_QUOTES)).strip().casefold()


@dataclass(frozen=True)
class ValidCitation:
    source: int
    external_id: str
    quote: str


@dataclass(frozen=True)
class CitationCheck:
    valid: list[ValidCitation]
    problems: list[str]


def check_citations(
    citations: list[tuple[int, str]], sources: list[RetrievedChunk], *, min_quote_chars: int = 8
) -> CitationCheck:
    valid: list[ValidCitation] = []
    problems: list[str] = []
    seen: set[tuple[int, str]] = set()
    for number, quote in citations:
        if not 1 <= number <= len(sources):
            problems.append(f"citation to S{number}, which does not exist")
            continue
        q = normalize(quote).strip("\"'. ")
        if len(q) < min_quote_chars:
            problems.append(f"quote for S{number} is too short to verify: {quote!r}")
            continue
        if q not in normalize(sources[number - 1].text):
            problems.append(f"quote for S{number} is not in that source: {quote!r}")
            continue
        if (number, q) in seen:
            continue
        seen.add((number, q))
        valid.append(ValidCitation(number, sources[number - 1].external_id, quote.strip()))
    return CitationCheck(valid, problems)
