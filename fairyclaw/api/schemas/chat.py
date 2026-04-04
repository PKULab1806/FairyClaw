# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Chat API schemas."""

from pydantic import BaseModel
from fairyclaw.api.schemas.common import Segment

class ChatRequest(BaseModel):
    """Request schema for one chat turn submission."""

    segments: list[Segment]
    
class ChatResponse(BaseModel):
    """Response schema for chat enqueue status."""

    status: str
    message: str
