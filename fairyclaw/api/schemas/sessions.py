# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Session API schemas."""

from pydantic import BaseModel, Field

class CreateSessionRequest(BaseModel):
    """Request schema for creating a new session."""

    platform: str = Field(min_length=1)
    title: str | None = None
    meta: dict = Field(default_factory=dict)

class CreateSessionResponse(BaseModel):
    """Response schema for newly created session."""

    session_id: str
    title: str | None
    created_at: int

class SessionListItem(BaseModel):
    """Schema for one session summary list item."""

    session_id: str
    title: str | None
    created_at: int
    last_activity_at: int
    event_count: int

class ListSessionResponse(BaseModel):
    """Response schema for session summary collection."""

    data: list[SessionListItem]
