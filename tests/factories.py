"""Builders for API payloads used across tests."""

from typing import Any


def conversation(
    external_id: str = "conv-1",
    *,
    customer_id: str | None = "cust-1",
    texts: tuple[str, ...] = (
        "Hi, thanks for contacting us.",
        "My bill went up and nobody told me about the new price.",
        "I'm sorry, let me check that for you.",
        "Thanks.",
    ),
    started_at: str = "2026-09-03T10:00:00",
    channel: str = "chat",
) -> dict[str, Any]:
    speakers = ("agent", "customer")
    return {
        "external_id": external_id,
        "customer_id": customer_id,
        "industry": "telecom",
        "channel": channel,
        "started_at": started_at,
        "turns": [
            {"speaker": speakers[i % 2], "text": t, "offset_s": i * 10} for i, t in enumerate(texts)
        ],
    }


def batch(*conversations: dict[str, Any]) -> dict[str, Any]:
    return {"conversations": list(conversations)}
