# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""OneBot gateway adapter."""

from __future__ import annotations

import base64
import logging
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, Request

from fairyclaw.config.settings import settings
from fairyclaw.core.domain import ContentSegment
from fairyclaw.core.gateway_protocol.models import GatewayInboundMessage, GatewayOutboundMessage, GatewaySenderRef, new_frame_id
from fairyclaw.gateway.adapters.base import GatewayAdapter
from fairyclaw.gateway.adapters.onebot_session_store import OnebotSessionStore
from fairyclaw.infrastructure.database.repository import GatewaySessionRouteRepository, SessionRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

MESSAGE_TYPE_PRIVATE = "private"
EVENT_POST_TYPE_MESSAGE = "message"
SEGMENT_TYPE_TEXT = "text"
SEGMENT_TYPE_FILE = "file"
SEGMENT_TYPE_IMAGE = "image"
BASE64_PROTOCOL_PREFIX = "base64://"
ONEBOT_SEND_GROUP_ENDPOINT = "send_group_msg"
ONEBOT_SEND_PRIVATE_ENDPOINT = "send_private_msg"


class OneBotGatewayAdapter(GatewayAdapter):
    """Receive OneBot events and bridge them into business sessions."""

    adapter_key = "onebot"
    kind = "onebot_v11"

    def __init__(self) -> None:
        self.onebot_api_base = os.getenv("ONEBOT_API_BASE", "http://localhost:3000")
        self.onebot_access_token = os.getenv("ONEBOT_ACCESS_TOKEN", "")
        self.onebot_allowed_user = os.getenv("ONEBOT_ALLOWED_USER", "")
        # From Settings so `config/fairyclaw.env` works without exporting to process env (os.getenv alone would miss it).
        self.onebot_session_cmd_prefix = (settings.onebot_session_cmd_prefix or "").strip() or "/sess"
        self.session_store = OnebotSessionStore()

    def _default_session_title(self, *, user_id: int, group_id: int | None) -> str:
        return f"OneBot User {user_id}" if group_id is None else f"OneBot User {user_id} (Group {group_id})"

    async def _open_onebot_session(
        self,
        *,
        user_id: int,
        group_id: int | None,
        sender_ref: dict[str, Any],
        title: str | None,
    ) -> str:
        return await self.runtime.open_session(
            adapter_key=self.adapter_key,
            platform="onebot",
            title=title or self._default_session_title(user_id=user_id, group_id=group_id),
            meta={"sender": sender_ref},
            sender_ref=sender_ref,
        )

    async def _resolve_session_id(self, *, user_id: int, group_id: int | None, sender_ref: dict[str, Any]) -> str:
        active_session_id = await self.session_store.get_active_session_id(
            adapter_key=self.adapter_key,
            sender_ref=sender_ref,
        )
        if active_session_id:
            async with AsyncSessionLocal() as db:
                route_repo = GatewaySessionRouteRepository(db)
                route = await route_repo.get_for_onebot_sender(session_id=active_session_id, sender_ref=sender_ref)
                if route is not None:
                    return active_session_id
            await self.session_store.clear_active_session_id(adapter_key=self.adapter_key, sender_ref=sender_ref)

        session_id = await self.runtime.find_session_by_sender(
            adapter_key=self.adapter_key,
            sender_ref=sender_ref,
        )
        if session_id is None:
            session_id = await self._open_onebot_session(
                user_id=user_id,
                group_id=group_id,
                sender_ref=sender_ref,
                title=None,
            )
        await self.session_store.set_active_session_id(
            adapter_key=self.adapter_key,
            sender_ref=sender_ref,
            session_id=session_id,
        )
        return session_id

    def _extract_text_message(self, message: Any) -> str | None:
        if isinstance(message, str):
            text = message.strip()
            return text or None
        if not isinstance(message, list):
            return None
        chunks: list[str] = []
        for segment in message:
            if not isinstance(segment, dict):
                return None
            if segment.get("type") != SEGMENT_TYPE_TEXT:
                return None
            data = segment.get("data", {})
            if not isinstance(data, dict):
                return None
            text = str(data.get("text") or "")
            if text:
                chunks.append(text)
        joined = "".join(chunks).strip()
        return joined or None

    def _parse_management_command(self, text: str) -> tuple[str, str] | None:
        if not text.startswith(self.onebot_session_cmd_prefix):
            return None
        tail = text[len(self.onebot_session_cmd_prefix) :].strip()
        if not tail:
            return "", ""
        parts = tail.split(maxsplit=1)
        command = parts[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""
        return command, argument

    async def _resolve_owned_onebot_session_id(
        self,
        *,
        needle: str,
        sender_ref: dict[str, Any],
        route_repo: GatewaySessionRouteRepository,
    ) -> tuple[str | None, str | None]:
        """Resolve sess_id or session title to an owned onebot session_id.

        Returns:
            (session_id, None) on success, or (None, user-facing error message).
        """
        raw = needle.strip()
        if not raw:
            return None, "empty"

        if raw.startswith("sess_"):
            route = await route_repo.get_for_onebot_sender(session_id=raw, sender_ref=sender_ref)
            if route is not None:
                return raw, None
            return None, f"会话不存在或不属于当前发送者: {raw}"

        items = await route_repo.list_sessions_for_onebot_sender(sender_ref=sender_ref)
        key = raw.lower()
        exact = [it for it in items if (it.title or "").strip().lower() == key]
        if len(exact) == 1:
            return exact[0].session_id, None
        if len(exact) > 1:
            ids = ", ".join(it.session_id for it in exact[:5])
            return None, f"标题重复，请用 session_id 指定: {ids}"

        prefix_hits = [
            it for it in items if (it.title or "").strip() and (it.title or "").strip().lower().startswith(key)
        ]
        if len(prefix_hits) == 1:
            return prefix_hits[0].session_id, None
        if len(prefix_hits) > 1:
            return None, "多个会话标题以该前缀开头，请用更完整的名称或 session_id。"

        return None, f"未找到会话: {raw}"

    async def _render_session_list(self, *, sender_ref: dict[str, Any]) -> str:
        active_session_id = await self.session_store.get_active_session_id(
            adapter_key=self.adapter_key,
            sender_ref=sender_ref,
        )
        async with AsyncSessionLocal() as db:
            route_repo = GatewaySessionRouteRepository(db)
            items = await route_repo.list_sessions_for_onebot_sender(sender_ref=sender_ref)
        if not items:
            return "当前没有可切换的会话。"
        lines = ["会话列表:"]
        for item in items[:20]:
            marker = "* " if item.session_id == active_session_id else "  "
            timestamp = item.updated_at.strftime("%Y-%m-%d %H:%M:%S") if item.updated_at else "-"
            title = item.title or "(untitled)"
            lines.append(f"{marker}{item.session_id} | {title} | {timestamp}")
        if len(items) > 20:
            lines.append(f"... 共 {len(items)} 个会话，仅显示前 20 个")
        return "\n".join(lines)

    def _management_help_text(self) -> str:
        prefix = self.onebot_session_cmd_prefix
        return "\n".join(
            [
                "会话管理指令:",
                f"{prefix} new [标题]",
                f"{prefix} ls",
                f"{prefix} checkout <session_id 或 标题>",
                f"{prefix} co <session_id 或 标题>",
                f"{prefix} rm <session_id 或 标题>  （永久删除该会话）",
            ]
        )

    async def _send_onebot_message(self, *, user_id: int, group_id: int | None, message: Any) -> None:
        endpoint = ONEBOT_SEND_GROUP_ENDPOINT if group_id is not None else ONEBOT_SEND_PRIVATE_ENDPOINT
        url = f"{self.onebot_api_base}/{endpoint}"
        payload: dict[str, Any] = {"message": message}
        if group_id is not None:
            payload["group_id"] = group_id
        else:
            payload["user_id"] = user_id
        headers = {"Authorization": f"Bearer {self.onebot_access_token}"} if self.onebot_access_token else {}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=headers, timeout=30.0)
            response.raise_for_status()

    async def _handle_management_command(
        self,
        *,
        user_id: int,
        group_id: int | None,
        sender_ref: dict[str, Any],
        text: str,
    ) -> bool:
        parsed = self._parse_management_command(text)
        if parsed is None:
            return False
        command, argument = parsed
        if command in {"", "help"}:
            await self._send_onebot_message(
                user_id=user_id,
                group_id=group_id,
                message=self._management_help_text(),
            )
            return True
        if command == "new":
            title = argument or None
            session_id = await self._open_onebot_session(
                user_id=user_id,
                group_id=group_id,
                sender_ref=sender_ref,
                title=title,
            )
            await self.session_store.set_active_session_id(
                adapter_key=self.adapter_key,
                sender_ref=sender_ref,
                session_id=session_id,
            )
            await self._send_onebot_message(
                user_id=user_id,
                group_id=group_id,
                message=f"已创建新会话并切换到: {session_id}",
            )
            return True
        if command == "ls":
            message = await self._render_session_list(sender_ref=sender_ref)
            await self._send_onebot_message(user_id=user_id, group_id=group_id, message=message)
            return True
        if command in {"checkout", "co"}:
            if not argument.strip():
                await self._send_onebot_message(
                    user_id=user_id,
                    group_id=group_id,
                    message=(
                        f"用法: {self.onebot_session_cmd_prefix} checkout <session_id 或 标题>\n"
                        f"示例: {self.onebot_session_cmd_prefix} checkout sess_abc...\n"
                        f"或: {self.onebot_session_cmd_prefix} checkout 我的工作"
                    ),
                )
                return True
            async with AsyncSessionLocal() as db:
                route_repo = GatewaySessionRouteRepository(db)
                target_session_id, err = await self._resolve_owned_onebot_session_id(
                    needle=argument,
                    sender_ref=sender_ref,
                    route_repo=route_repo,
                )
            if err or not target_session_id:
                await self._send_onebot_message(
                    user_id=user_id,
                    group_id=group_id,
                    message=err or "无法解析会话",
                )
                return True
            await self.session_store.set_active_session_id(
                adapter_key=self.adapter_key,
                sender_ref=sender_ref,
                session_id=target_session_id,
            )
            await self._send_onebot_message(
                user_id=user_id,
                group_id=group_id,
                message=f"已切换到会话: {target_session_id}",
            )
            return True
        if command == "rm":
            if not argument.strip():
                await self._send_onebot_message(
                    user_id=user_id,
                    group_id=group_id,
                    message=(
                        f"用法: {self.onebot_session_cmd_prefix} rm <session_id 或 标题>\n"
                        "将永久删除该会话及其消息与文件。"
                    ),
                )
                return True
            async with AsyncSessionLocal() as db:
                route_repo = GatewaySessionRouteRepository(db)
                target_session_id, err = await self._resolve_owned_onebot_session_id(
                    needle=argument,
                    sender_ref=sender_ref,
                    route_repo=route_repo,
                )
                if err or not target_session_id:
                    await self._send_onebot_message(
                        user_id=user_id,
                        group_id=group_id,
                        message=err or "无法解析会话",
                    )
                    return True
                active = await self.session_store.get_active_session_id(
                    adapter_key=self.adapter_key,
                    sender_ref=sender_ref,
                )
                if active == target_session_id:
                    await self.session_store.clear_active_session_id(
                        adapter_key=self.adapter_key,
                        sender_ref=sender_ref,
                    )
                session_repo = SessionRepository(db)
                deleted = await session_repo.delete(target_session_id)
            if not deleted:
                await self._send_onebot_message(
                    user_id=user_id,
                    group_id=group_id,
                    message=f"删除失败（会话可能已不存在）: {target_session_id}",
                )
                return True
            await self._send_onebot_message(
                user_id=user_id,
                group_id=group_id,
                message=f"已删除会话: {target_session_id}",
            )
            return True
        await self._send_onebot_message(
            user_id=user_id,
            group_id=group_id,
            message=self._management_help_text(),
        )
        return True

    async def _upload_remote_file(self, *, session_id: str, url: str, filename: str) -> str | None:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=30.0)
            response.raise_for_status()
            return await self.runtime.upload_file(
                session_id=session_id,
                adapter_key=self.adapter_key,
                message_id=new_frame_id("onebot_file"),
                content=response.content,
                filename=filename,
                mime_type=response.headers.get("content-type"),
            )

    async def _convert_message_to_segments(self, *, session_id: str, message: Any) -> list[ContentSegment]:
        segments: list[ContentSegment] = []
        if isinstance(message, str):
            cq_pattern = re.compile(r"\[CQ:(image|file),.*?file=(.*?)\]")
            last_idx = 0
            for match in cq_pattern.finditer(message):
                text_before = message[last_idx:match.start()].strip()
                if text_before:
                    segments.append(ContentSegment.text_segment(text_before))
                url_or_file = match.group(2)
                url = url_or_file.split(",")[0]
                file_id = await self._upload_remote_file(session_id=session_id, url=url, filename="upload")
                if file_id:
                    segments.append(ContentSegment.file_segment(file_id))
                last_idx = match.end()
            text_after = message[last_idx:].strip()
            if text_after:
                segments.append(ContentSegment.text_segment(text_after))
            return segments

        if isinstance(message, list):
            for segment in message:
                if not isinstance(segment, dict):
                    continue
                seg_type = segment.get("type")
                if seg_type == SEGMENT_TYPE_TEXT:
                    text = str(segment.get("data", {}).get("text") or "").strip()
                    if text:
                        segments.append(ContentSegment.text_segment(text))
                elif seg_type in {SEGMENT_TYPE_IMAGE, SEGMENT_TYPE_FILE}:
                    data = segment.get("data", {})
                    if not isinstance(data, dict):
                        continue
                    url = data.get("url") or data.get("file")
                    if not isinstance(url, str) or not url:
                        continue
                    filename = str(data.get("name") or "upload")
                    file_id = await self._upload_remote_file(session_id=session_id, url=url, filename=filename)
                    if file_id:
                        segments.append(ContentSegment.file_segment(file_id))
        return segments

    async def _process_inbound_message(
        self,
        *,
        user_id: int,
        group_id: int | None,
        self_id: str | None,
        message: Any,
    ) -> None:
        sender_ref = {
            "platform": "onebot",
            "user_id": str(user_id),
            "group_id": str(group_id) if group_id is not None else None,
            "self_id": self_id,
        }
        command_text = self._extract_text_message(message)
        if command_text is not None:
            handled = await self._handle_management_command(
                user_id=user_id,
                group_id=group_id,
                sender_ref=sender_ref,
                text=command_text,
            )
            if handled:
                return

        session_id = await self._resolve_session_id(
            user_id=user_id,
            group_id=group_id,
            sender_ref=sender_ref,
        )

        inbound_segments = await self._convert_message_to_segments(session_id=session_id, message=message)
        has_text = any(segment.type.value == SEGMENT_TYPE_TEXT and (segment.text or "").strip() for segment in inbound_segments)
        if not inbound_segments:
            return
        await self.runtime.submit_inbound(
            GatewayInboundMessage(
                session_id=session_id,
                adapter_key=self.adapter_key,
                segments=tuple(inbound_segments),
                trigger_turn=has_text,
                sender=GatewaySenderRef.from_dict(sender_ref),
                meta={"message_id": new_frame_id("onebot_msg")},
            )
        )

    def build_router(self) -> APIRouter:
        router = APIRouter()

        @router.post("/")
        @router.post("/onebot/event")
        async def onebot_event(request: Request) -> dict[str, str]:
            data = await request.json()
            post_type = data.get("post_type")
            if post_type != EVENT_POST_TYPE_MESSAGE:
                return {"status": "ignored", "reason": "not a message event"}
            user_id = data.get("user_id")
            self_id = data.get("self_id")
            group_id = data.get("group_id")
            message = data.get("message")
            if not user_id:
                return {"status": "ignored", "reason": "no user_id"}
            if self_id and str(user_id) == str(self_id):
                return {"status": "ignored", "reason": "self message"}
            message_type = data.get("message_type")
            if self.onebot_allowed_user:
                if message_type != MESSAGE_TYPE_PRIVATE:
                    return {"status": "ignored", "reason": "only private allowed"}
                if str(user_id) != str(self.onebot_allowed_user):
                    return {"status": "ignored", "reason": "user not allowed"}
            await self._process_inbound_message(
                user_id=int(user_id),
                group_id=int(group_id) if group_id is not None else None,
                self_id=str(self_id) if self_id is not None else None,
                message=message,
            )
            return {"status": "ok"}

        return router

    async def send(self, outbound: GatewayOutboundMessage) -> None:
        _, sender_ref = await self.runtime.route_store.resolve(outbound.session_id)
        user_id_raw = sender_ref.get("user_id")
        if user_id_raw is None:
            raise RuntimeError(f"Missing OneBot sender route for session: {outbound.session_id}")
        user_id = int(str(user_id_raw))
        group_raw = sender_ref.get("group_id")
        group_id = int(str(group_raw)) if group_raw is not None and str(group_raw).strip() else None
        if outbound.kind == "text":
            await self._send_onebot_message(user_id=user_id, group_id=group_id, message=outbound.content.get("text") or "")
            return
        if outbound.kind == "file":
            file_id = outbound.content.get("file_id")
            if not isinstance(file_id, str) or not file_id:
                raise RuntimeError("Missing file_id in outbound file payload")
            content, filename, _ = await self.runtime.download_file(session_id=outbound.session_id, file_id=file_id)
            message = [
                {"type": "text", "data": {"text": f"收到文件: {filename or file_id}\n"}},
                {
                    "type": "file",
                    "data": {
                        "file": f"{BASE64_PROTOCOL_PREFIX}{base64.b64encode(content).decode('utf-8')}",
                        "name": filename or file_id,
                    },
                },
            ]
            await self._send_onebot_message(user_id=user_id, group_id=group_id, message=message)
            return
        raise RuntimeError(f"Unsupported outbound kind for OneBot adapter: {outbound.kind}")
