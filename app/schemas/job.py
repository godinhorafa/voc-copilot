import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db.models import JobStatus


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: JobStatus
    total: int
    processed: int
    failed: int
    attempts: int
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
