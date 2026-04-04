# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Strongly typed history intermediate representation for planner context."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from fairyclaw.core.domain import ContentSegment, SegmentType

_FILE_ATTACH_USAGE = (
    "会话文件 ID（请完整复制，勿截断或编造）：{file_id}\n"
    "向子代理传递该文件时，请将此 ID 原样写入 delegate_task 的 attachments 数组；"
    "可先调用 list_session_files 核对。禁止使用 file_id_ 前缀或自拟 ID。"
)


def _file_segment_llm_text(segment: ContentSegment) -> str:
    """Single user-visible line block: type hint + file id + delegate_task usage (no API file part)."""
    hint = (segment.file_kind_description or "").strip()
    fid = (segment.file_id or "").strip()
    usage = _FILE_ATTACH_USAGE.format(file_id=fid)
    if hint:
        return f"{hint}\n\n{usage}"
    return f"用户上传了一个会话文件。\n\n{usage}"


def _segments_need_openai_multipart(segments: tuple[ContentSegment, ...]) -> bool:
    """If True, message content must stay a list (e.g. images)."""
    for seg in segments:
        if seg.type == SegmentType.IMAGE_URL:
            return True
        if seg.type not in (SegmentType.TEXT, SegmentType.FILE):
            return True
    return False


class SessionMessageRole(str, Enum):
    """Allowed role values for one session message block."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"

    @classmethod
    def from_value(cls, value: str) -> "SessionMessageRole":
        """Normalize arbitrary role string into supported session role."""
        role = value.strip().lower()
        if role == cls.SYSTEM.value:
            return cls.SYSTEM
        if role == cls.USER.value:
            return cls.USER
        return cls.ASSISTANT


@dataclass(frozen=True)
class TextBody:
    """Plain text body for one session message block."""

    text: str

    def as_plain_text(self) -> str:
        """Return body as plain text."""
        return self.text


@dataclass(frozen=True)
class SegmentsBody:
    """Structured segment body for one session message block."""

    segments: tuple[ContentSegment, ...]

    def as_plain_text(self) -> str:
        """Return text-only projection from structured segments."""
        chunks: list[str] = []
        for segment in self.segments:
            if segment.type == SegmentType.TEXT and segment.text:
                chunks.append(segment.text)
            elif segment.type == SegmentType.FILE and segment.file_id:
                chunks.append(_file_segment_llm_text(segment))
        return "\n".join(chunks).strip()

    def as_openai_content(self) -> str | list[dict[str, object]]:
        """Serialize structured body for the LLM: FILE becomes text (id + usage), not a native file part."""
        if not _segments_need_openai_multipart(self.segments):
            parts: list[str] = []
            for segment in self.segments:
                if segment.type == SegmentType.TEXT:
                    if segment.text:
                        parts.append(segment.text)
                elif segment.type == SegmentType.FILE and segment.file_id:
                    parts.append(_file_segment_llm_text(segment))
            return "\n\n".join(parts) if parts else ""

        items: list[dict[str, object]] = []
        for segment in self.segments:
            if segment.type == SegmentType.FILE and segment.file_id:
                items.append({"type": "text", "text": _file_segment_llm_text(segment)})
            else:
                items.append(segment.to_dict())
        return items


MessageBody = TextBody | SegmentsBody


@dataclass(frozen=True)
class SessionMessageBlock:
    """One strongly typed session message block."""

    role: SessionMessageRole
    body: MessageBody

    def as_plain_text(self) -> str:
        """Return message as plain text projection."""
        return self.body.as_plain_text()

    def as_openai_content(self) -> str | list[dict[str, object]]:
        """Return OpenAI content representation for this message block."""
        if isinstance(self.body, TextBody):
            return self.body.text
        return self.body.as_openai_content()

    @classmethod
    def from_segments(
        cls,
        role: SessionMessageRole | str,
        segments: Iterable[ContentSegment],
    ) -> "SessionMessageBlock | None":
        """Build one message block from typed content segments."""
        normalized_segments = tuple(segments)
        if not normalized_segments:
            return None
        normalized_role = role if isinstance(role, SessionMessageRole) else SessionMessageRole.from_value(role)
        if len(normalized_segments) == 1 and normalized_segments[0].type == SegmentType.TEXT:
            return cls(role=normalized_role, body=TextBody(text=normalized_segments[0].text or ""))
        return cls(role=normalized_role, body=SegmentsBody(segments=normalized_segments))


@dataclass(frozen=True)
class ToolCallRound:
    """Normalized tool-call roundtrip history entry."""

    tool_name: str
    call_id: str
    arguments_json: str
    tool_result: str

    @classmethod
    def from_persisted(
        cls,
        event_id: str,
        tool_name: str,
        tool_args: object,
        tool_result: object,
    ) -> "ToolCallRound":
        """Build one tool round from persisted operation-event fields."""
        call_id = cls._extract_tool_call_id(event_id, tool_args)
        arguments_json = cls._extract_arguments_json(tool_args)
        return cls(
            tool_name=tool_name,
            call_id=call_id,
            arguments_json=arguments_json,
            tool_result="" if tool_result is None else str(tool_result),
        )

    @staticmethod
    def _extract_tool_call_id(event_id: str, tool_args: object) -> str:
        if isinstance(tool_args, dict):
            tool_call_id = tool_args.get("tool_call_id")
            if isinstance(tool_call_id, str) and tool_call_id.strip():
                return tool_call_id
        suffix = event_id[-6:] if event_id else "hist"
        return f"tc_h_{suffix}"

    @staticmethod
    def _extract_arguments_json(tool_args: object) -> str:
        if isinstance(tool_args, dict):
            arguments_json = tool_args.get("arguments_json")
            if isinstance(arguments_json, str) and arguments_json.strip():
                return arguments_json
            return json.dumps(tool_args, ensure_ascii=False)
        if isinstance(tool_args, str) and tool_args.strip():
            return tool_args
        return "{}"


@dataclass(frozen=True)
class UserTurn:
    """Strongly typed user turn wrapper."""

    message: SessionMessageBlock

    @classmethod
    def from_segments(cls, segments: Iterable[ContentSegment]) -> "UserTurn | None":
        """Build one typed user turn from typed content segments."""
        message = SessionMessageBlock.from_segments(SessionMessageRole.USER, segments)
        if message is None:
            return None
        return cls(message=message)


ChatHistoryItem = SessionMessageBlock | ToolCallRound
