import uuid
from datetime import UTC, datetime

from app.answering.citations import check_citations, normalize
from app.retrieval.types import RetrievedChunk, conversation_ranking


def _chunk(external_id: str, text: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
        external_id=external_id,
        started_at=datetime(2026, 9, 3, tzinfo=UTC),
        text=text,
        score=0.9,
    )


SOURCES = [
    _chunk("c1", "customer: My bill went up from $29.90 to $34.38 and nobody told me."),
    _chunk("c2", "customer: I'm moving to   NovaTel, they've been calling me."),
]


def test_valid_citations_map_to_conversations() -> None:
    check = check_citations([(1, "nobody told me"), (2, "moving to NovaTel")], SOURCES)
    assert check.problems == []
    assert [(c.source, c.external_id) for c in check.valid] == [(1, "c1"), (2, "c2")]


def test_quote_matching_ignores_case_whitespace_and_typographic_quotes() -> None:
    check = check_citations([(2, "I\N{RIGHT SINGLE QUOTATION MARK}M MOVING TO NovaTel")], SOURCES)
    assert check.problems == []


def test_rejects_unknown_source_and_fabricated_quote() -> None:
    check = check_citations([(3, "anything at all"), (1, "they doubled the price")], SOURCES)
    assert check.valid == []
    assert check.problems[0] == "citation to S3, which does not exist"
    assert "not in that source" in check.problems[1]


def test_rejects_quote_from_the_wrong_source() -> None:
    check = check_citations([(1, "moving to NovaTel")], SOURCES)
    assert check.valid == []


def test_rejects_quotes_too_short_to_verify() -> None:
    check = check_citations([(1, "bill")], SOURCES)
    assert "too short" in check.problems[0]


def test_duplicates_are_collapsed() -> None:
    check = check_citations([(1, "nobody told me"), (1, "Nobody told me.")], SOURCES)
    assert len(check.valid) == 1


def test_normalize() -> None:
    assert normalize("  A\n“B”  ") == 'a "b"'


def test_conversation_ranking_dedups_in_order() -> None:
    chunks = [_chunk("a", "x"), _chunk("b", "y"), _chunk("a", "z"), _chunk("c", "w")]
    assert conversation_ranking(chunks) == ["a", "b", "c"]
