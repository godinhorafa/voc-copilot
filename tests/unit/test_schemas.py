from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.conversation import ConversationIn, IngestRequest
from tests.factories import batch, conversation


def test_naive_timestamp_is_utc() -> None:
    conv = ConversationIn.model_validate(conversation(started_at="2026-09-03T10:00:00"))
    assert conv.started_at == datetime(2026, 9, 3, 10, tzinfo=UTC)


def test_aware_timestamp_is_normalized_to_utc() -> None:
    conv = ConversationIn.model_validate(conversation(started_at="2026-09-03T10:00:00-03:00"))
    assert conv.started_at == datetime(2026, 9, 3, 13, tzinfo=UTC)


def test_content_hash_ignores_external_id_and_tracks_content() -> None:
    a = ConversationIn.model_validate(conversation("a"))
    b = ConversationIn.model_validate(conversation("b"))
    c = ConversationIn.model_validate(conversation("a", texts=("Hello", "Different")))
    assert a.content_hash() == b.content_hash()
    assert a.content_hash() != c.content_hash()


def test_equivalent_timestamps_hash_equal() -> None:
    a = ConversationIn.model_validate(conversation(started_at="2026-09-03T10:00:00Z"))
    b = ConversationIn.model_validate(conversation(started_at="2026-09-03T07:00:00-03:00"))
    assert a.content_hash() == b.content_hash()


def test_rejects_duplicate_external_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate external_id"):
        IngestRequest.model_validate(batch(conversation("x"), conversation("x")))


@pytest.mark.parametrize("bad_id", ["", "has space", "a/b", "x" * 129])
def test_rejects_bad_external_ids(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        ConversationIn.model_validate(conversation(bad_id))


def test_rejects_unknown_fields() -> None:
    payload = conversation() | {"labels": {"sentiment": "negative"}}
    with pytest.raises(ValidationError, match="labels"):
        ConversationIn.model_validate(payload)
