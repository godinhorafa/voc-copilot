from dataclasses import dataclass

import pytest

from app.ingestion.chunking import chunk_turns


@dataclass
class T:
    speaker: str
    text: str


def _turns(*word_counts: int) -> list[T]:
    speakers = ("agent", "customer")
    return [
        T(speakers[i % 2], " ".join(f"w{i}" for _ in range(n))) for i, n in enumerate(word_counts)
    ]


def test_short_conversation_is_one_chunk() -> None:
    chunks = chunk_turns([T("agent", "Hello there"), T("customer", "My bill is wrong")])
    assert len(chunks) == 1
    assert chunks[0].text == "agent: Hello there\ncustomer: My bill is wrong"
    assert (chunks[0].turn_start, chunks[0].turn_end) == (0, 1)


def test_chunks_respect_budget_and_overlap() -> None:
    chunks = chunk_turns(_turns(40, 40, 40, 40, 40), max_words=100, overlap_turns=1)
    assert [(c.turn_start, c.turn_end) for c in chunks] == [(0, 1), (1, 2), (2, 3), (3, 4)]


def test_no_overlap() -> None:
    chunks = chunk_turns(_turns(40, 40, 40, 40), max_words=100, overlap_turns=0)
    assert [(c.turn_start, c.turn_end) for c in chunks] == [(0, 1), (2, 3)]


def test_every_turn_is_covered() -> None:
    turns = _turns(5, 80, 12, 33, 7, 90, 2, 60, 18)
    chunks = chunk_turns(turns, max_words=100, overlap_turns=1)
    covered = {i for c in chunks for i in range(c.turn_start, c.turn_end + 1)}
    assert covered == set(range(len(turns)))


def test_long_turn_is_split_and_progress_continues() -> None:
    chunks = chunk_turns(_turns(10, 250, 10), max_words=100, overlap_turns=1)
    long_parts = [c for c in chunks if c.turn_start == c.turn_end == 1]
    assert len(long_parts) == 3
    assert all(c.text.startswith("customer: ") for c in long_parts)
    assert chunks[-1].turn_end == 2


def test_overlap_larger_than_chunk_still_progresses() -> None:
    chunks = chunk_turns(_turns(60, 60, 60), max_words=100, overlap_turns=5)
    assert [(c.turn_start, c.turn_end) for c in chunks] == [(0, 0), (1, 1), (2, 2)]


def test_empty_input() -> None:
    assert chunk_turns([]) == []


@pytest.mark.parametrize(("max_words", "overlap"), [(0, 1), (10, -1)])
def test_rejects_bad_params(max_words: int, overlap: int) -> None:
    with pytest.raises(ValueError, match="must be"):
        chunk_turns(_turns(5), max_words=max_words, overlap_turns=overlap)
