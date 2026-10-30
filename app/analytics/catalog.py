"""What text-to-SQL may query: the views in the `analytics` schema.

Single source of truth for the SQL guard's table allowlist and for the schema
description in the prompt. A test checks it against the real view columns.
"""

from dataclasses import dataclass

SCHEMA = "analytics"
ROLE = "voc_analytics"


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    description: str


@dataclass(frozen=True)
class View:
    name: str
    description: str
    columns: tuple[Column, ...]

    @property
    def qualified(self) -> str:
        return f"{SCHEMA}.{self.name}"


VIEWS: tuple[View, ...] = (
    View(
        "conversations",
        "One row per customer service conversation, with LLM-extracted insights "
        "(insight columns are NULL until extraction has run).",
        (
            Column("conversation_id", "text", "Conversation id"),
            Column("customer_id", "text", "Customer id; join with customers"),
            Column("industry", "text", "telecom | banking | ecommerce"),
            Column("channel", "text", "chat | voice"),
            Column("started_at", "timestamptz", "When the conversation started (UTC)"),
            Column("contact_reason", "text", "Main reason for contact, a taxonomy id"),
            Column("sentiment", "text", "negative | neutral | positive (customer, at the end)"),
            Column("resolved", "boolean", "Issue solved in this contact"),
            Column("churn_risk", "text", "low | medium | high"),
            Column("competitors", "text[]", "Competitor names mentioned by the customer"),
            Column("summary", "text", "One-sentence summary"),
        ),
    ),
    View(
        "customers",
        "One row per customer, from the CRM / billing system.",
        (
            Column("customer_id", "text", "Customer id"),
            Column("industry", "text", "telecom | banking | ecommerce"),
            Column("plan_code", "text", "Current plan code"),
            Column("signup_at", "date", "Signup date"),
            Column("churned_at", "date", "Date the customer left; NULL if active"),
            Column("churn_reason", "text", "Recorded churn reason; NULL if active"),
            Column("churned", "boolean", "churned_at IS NOT NULL"),
        ),
    ),
    View(
        "competitor_mentions",
        "One row per competitor mentioned in a conversation (unnested from "
        "conversations.competitors), for counting mentions.",
        (
            Column("conversation_id", "text", "Conversation id"),
            Column("customer_id", "text", "Customer id"),
            Column("industry", "text", "telecom | banking | ecommerce"),
            Column("started_at", "timestamptz", "When the conversation started (UTC)"),
            Column("competitor", "text", "Competitor name as mentioned"),
        ),
    ),
)

ALLOWED_TABLES = frozenset(v.qualified for v in VIEWS)


def describe() -> str:
    """Schema description for the text-to-SQL prompt."""
    blocks = []
    for view in VIEWS:
        cols = "\n".join(f"  - {c.name} ({c.type}): {c.description}" for c in view.columns)
        blocks.append(f"{view.qualified}: {view.description}\n{cols}")
    return "\n\n".join(blocks)
