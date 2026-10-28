"""Reciprocal Rank Fusion (Cormack et al., 2009).

score(d) = sum over rankings of 1 / (k + rank(d)), rank starting at 1. Uses
ranks only, so lists with incomparable scores (cosine similarity, ts_rank) can
be combined without calibration. k = 60 is the value from the paper; it damps
the advantage of the very top ranks.
"""

from collections.abc import Hashable, Sequence

DEFAULT_K = 60


def rrf[T: Hashable](
    rankings: Sequence[Sequence[T]], *, k: int = DEFAULT_K
) -> list[tuple[T, float]]:
    if k < 0:
        raise ValueError("k must be >= 0")
    scores: dict[T, float] = {}
    first_seen: dict[T, int] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
            first_seen.setdefault(item, len(first_seen))
    # Ties broken by first appearance, so the result is deterministic.
    return sorted(scores.items(), key=lambda kv: (-kv[1], first_seen[kv[0]]))
