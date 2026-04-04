# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Common API schemas."""

from typing import Any, Optional, Dict
from pydantic import BaseModel, Field

class Segment(BaseModel):
    """Unified API segment schema for multimodal message payloads."""

    type: str = Field(..., description="text, file, code_block, image_url")
    content: Optional[str] = None
    file_id: Optional[str] = None
    image_url: Optional[Dict[str, Any]] = None
    path: Optional[str] = None
    absolute_path: Optional[str] = None
    name: Optional[str] = None
    size: Optional[int] = None
    mime_type: Optional[str] = None
