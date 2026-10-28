from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.ask import AskRequest


def test_defaults_and_utc() -> None:
    req = AskRequest.model_validate({"question": "Why?", "start": "2026-09-01T00:00:00"})
    assert req.k == 8
    assert req.start == datetime(2026, 9, 1, tzinfo=UTC)


def test_rejects_inverted_window() -> None:
    with pytest.raises(ValidationError, match="end must be after start"):
        AskRequest.model_validate(
            {"question": "Why?", "start": "2026-09-02T00:00:00Z", "end": "2026-09-01T00:00:00Z"}
        )


@pytest.mark.parametrize("k", [0, 31])
def test_k_bounds(k: int) -> None:
    with pytest.raises(ValidationError):
        AskRequest.model_validate({"question": "Why?", "k": k})
