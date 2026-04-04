# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""File API schemas."""

from pydantic import BaseModel

class UploadFileResponse(BaseModel):
    """Response schema after successful file upload."""

    file_id: str
    filename: str
    size: int
    created_at: int

class FileInfoResponse(BaseModel):
    """Response schema for file metadata and download endpoint."""

    file_id: str
    session_id: str
    filename: str
    size: int
    created_at: int
    mime_type: str | None
    download_url: str
