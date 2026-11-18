import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Role


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128, description="What the key is for")
    role: Role


class ApiKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    role: Role
    prefix: str
    created_at: datetime
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyOut):
    key: str = Field(description="Shown only in this response; store it now")
