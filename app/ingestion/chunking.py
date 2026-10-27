"""Turn-aware chunking.

Chunks are built from whole turns, never splitting a turn unless it alone is
longer than the budget. Each chunk keeps speaker labels ("customer: ...") so
retrieval sees who said what, and consecutive chunks overlap by a number of
turns so a question and its answer are not separated by a chunk boundary.

Sizes are measured in words as a cheap, model-independent proxy for tokens.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

_WORD = re.compile(r"\S+")


class TurnLike(Protocol):
    @property
    def speaker(self) -> str: ...

    @property
    def text(self) -> str: ...


@dataclass(frozen=True)
class ChunkSpec:
    turn_start: int  # inclusive index into the turn list
    turn_end: int  # inclusive
    text: str


def _words(text: str) -> int:
    return len(_WORD.findall(text))


def _format(speaker: str, text: str) -> str:
    return f"{speaker}: {text.strip()}"


def _split_long_turn(idx: int, speaker: str, text: str, max_words: int) -> list[ChunkSpec]:
    words = _WORD.findall(text)
    return [
        ChunkSpec(idx, idx, _format(speaker, " ".join(words[i : i + max_words])))
        for i in range(0, len(words), max_words)
    ]


def chunk_turns(
    turns: Sequence[TurnLike], *, max_words: int = 160, overlap_turns: int = 1
) -> list[ChunkSpec]:
    if max_words <= 0:
        raise ValueError("max_words must be positive")
    if overlap_turns < 0:
        raise ValueError("overlap_turns must be >= 0")

    chunks: list[ChunkSpec] = []
    start = 0
    n = len(turns)
    while start < n:
        first = turns[start]
        if _words(first.text) > max_words:
            chunks.extend(_split_long_turn(start, first.speaker, first.text, max_words))
            start += 1
            continue

        end, size = start, _words(first.text)
        while end + 1 < n and size + _words(turns[end + 1].text) <= max_words:
            end += 1
            size += _words(turns[end].text)

        text = "\n".join(_format(t.speaker, t.text) for t in turns[start : end + 1])
        chunks.append(ChunkSpec(start, end, text))
        if end == n - 1:
            break
        # Step back for overlap, but always make progress.
        start = max(end + 1 - overlap_turns, start + 1)
    return chunks
