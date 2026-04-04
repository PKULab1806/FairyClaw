# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Core domain models."""

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

HISTORY_TYPE_SESSION = "session"
HISTORY_TYPE_OPERATION = "operation"

class EventType(Enum):
    """Enumerate persisted event categories."""

    SESSION_EVENT = "session_event"
    OPERATION_EVENT = "operation_event"

class Role(Enum):
    """Enumerate conversation roles used in session history."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

class SegmentType(Enum):
    """Enumerate supported content segment kinds."""

    TEXT = "text"
    IMAGE_URL = "image_url"
    FILE = "file"
    CODE_BLOCK = "code_block"


@dataclass
class ContentSegment:
    """Represent one structured conversation content segment."""

    type: SegmentType
    text: Optional[str] = None
    image_url: Optional[Dict[str, Any]] = None
    file_id: Optional[str] = None
    file_kind_description: Optional[str] = None

    @classmethod
    def text_segment(cls, text: str) -> "ContentSegment":
        """Build a text content segment.

        Args:
            text (str): Text payload.

        Returns:
            ContentSegment: Segment instance with TEXT type.
        """
        return cls(type=SegmentType.TEXT, text=text)

    @classmethod
    def image_url_segment(cls, url: str) -> "ContentSegment":
        """Build an image-url content segment.

        Args:
            url (str): Image URL string.

        Returns:
            ContentSegment: Segment instance with IMAGE_URL type.
        """
        return cls(type=SegmentType.IMAGE_URL, image_url={"url": url})

    @classmethod
    def file_segment(cls, file_id: str, *, file_kind_description: Optional[str] = None) -> "ContentSegment":
        """Build a file-reference content segment.

        Args:
            file_id (str): Session file identifier.
            file_kind_description (Optional[str]): Short hint for the model (e.g. from magic-byte sniff).

        Returns:
            ContentSegment: Segment instance with FILE type.
        """
        return cls(type=SegmentType.FILE, file_id=file_id, file_kind_description=file_kind_description)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize segment to JSON-compatible dictionary.

        Returns:
            Dict[str, Any]: Serialized segment mapping.
        """
        data: Dict[str, Any] = {"type": self.type.value}
        if self.text is not None:
            data["text"] = self.text
        if self.image_url is not None:
            data["image_url"] = self.image_url
        if self.file_id is not None:
            data["file_id"] = self.file_id
        if self.file_kind_description is not None:
            data["file_kind_description"] = self.file_kind_description
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ContentSegment":
        """Deserialize ContentSegment from dictionary payload.

        Args:
            data (Dict[str, Any]): Serialized segment mapping.

        Returns:
            ContentSegment: Reconstructed segment object.
        """
        return cls(
            type=SegmentType(str(data.get("type"))),
            text=data.get("text"),
            image_url=data.get("image_url"),
            file_id=data.get("file_id"),
            file_kind_description=data.get("file_kind_description"),
        )

@dataclass
class VirtualFile:
    """Represent file entity value object in domain layer."""

    id: str
    filename: str
    content: bytes
    size: int
    mime_type: Optional[str] = None
    created_at: dt.datetime = field(default_factory=dt.datetime.utcnow)

@dataclass
class ToolCall:
    """Represent one tool invocation request."""

    name: str
    arguments: Dict[str, Any]

@dataclass
class Event:
    """Represent base event shared fields."""

    id: str
    session_id: str
    type: EventType
    timestamp: dt.datetime

@dataclass
class SessionEvent(Event):
    """Represent user-visible session conversation event."""

    role: Role
    content: List[Dict[str, Any]]

@dataclass
class OperationEvent(Event):
    """Represent tool execution operation event."""

    tool_name: str
    tool_args: Dict[str, Any]
    tool_result: Any


@dataclass
class SessionHistoryEntry:
    """Represent normalized session-history message entry."""

    role: str
    content: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        """Serialize session history entry.

        Returns:
            Dict[str, Any]: Unified session history dictionary.
        """
        return {
            "type": HISTORY_TYPE_SESSION,
            "role": self.role,
            "content": self.content,
        }


@dataclass
class OperationHistoryEntry:
    """Represent normalized operation-history entry."""

    event_id: str
    tool_name: str
    tool_args: Dict[str, Any]
    tool_result: Any

    def to_dict(self) -> Dict[str, Any]:
        """Serialize operation history entry.

        Returns:
            Dict[str, Any]: Unified operation history dictionary.
        """
        return {
            "type": HISTORY_TYPE_OPERATION,
            "id": self.event_id,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "tool_result": self.tool_result,
        }

@dataclass
class Session:
    """Represent session aggregate root in domain model."""

    id: str
    platform: str
    title: Optional[str]
    created_at: dt.datetime
    updated_at: dt.datetime
    events: List[Event] = field(default_factory=list)
    files: List[VirtualFile] = field(default_factory=list)
