from typing import Any

from sqlalchemy import Select

from app.db.models import Conversation
from app.retrieval.types import SearchFilters


def apply_filters(stmt: Select[Any], filters: SearchFilters) -> Select[Any]:
    """Restrict a query that joins Conversation to the filter window."""
    if filters.industry:
        stmt = stmt.where(Conversation.industry == filters.industry)
    if filters.start:
        stmt = stmt.where(Conversation.started_at >= filters.start)
    if filters.end:
        stmt = stmt.where(Conversation.started_at < filters.end)
    return stmt
