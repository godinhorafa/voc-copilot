import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import Role


class UserCreate(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Role


class UserUpdate(BaseModel):
    role: Role


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    role: Role
    created_at: datetime


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str


class MeOut(BaseModel):
    """Who the caller is, for the UI's account menu and role-aware navigation."""

    auth: Literal["oidc", "api_key"]
    role: Role
    email: str | None
    tenant: TenantOut
