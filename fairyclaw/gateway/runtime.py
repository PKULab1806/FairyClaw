# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Gateway runtime orchestration."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter

from fairyclaw.core.gateway_protocol.models import GatewayInboundMessage, GatewayOutboundMessage
from fairyclaw.gateway.bridge.ws_client import WsBridgeClient
from fairyclaw.gateway.route_store import GatewaySessionRouteStore

logger = logging.getLogger(__name__)


class GatewayRuntime:
    """Own adapter registry and the bridge client."""

    def __init__(self, adapters: list["GatewayAdapter"]) -> None:
        self.adapters = {adapter.adapter_key: adapter for adapter in adapters}
        self.route_store = GatewaySessionRouteStore()
        self.bridge = WsBridgeClient(runtime=self)

    async def start(self) -> None:
        for adapter in self.adapters.values():
            await adapter.start(self)
        await self.bridge.start()

    async def stop(self) -> None:
        await self.bridge.stop()
        for adapter in self.adapters.values():
            await adapter.stop()

    async def open_session(
        self,
        *,
        adapter_key: str,
        platform: str,
        title: str | None,
        meta: dict | None = None,
        sender_ref: dict[str, Any] | None = None,
    ) -> str:
        session_id = await self.bridge.open_session(
            adapter_key=adapter_key,
            platform=platform,
            title=title,
            meta=meta,
        )
        await self.route_store.bind(
            session_id=session_id,
            adapter_key=adapter_key,
            sender_ref=sender_ref,
        )
        return session_id

    async def submit_inbound(self, message: GatewayInboundMessage) -> None:
        await self.bridge.send_inbound(message)
        await self.route_store.bind(
            session_id=message.session_id,
            adapter_key=message.adapter_key,
            sender_ref=message.sender.to_dict() if message.sender is not None else None,
        )

    async def bind_sub_session(self, *, session_id: str, parent_session_id: str) -> None:
        await self.route_store.bind(
            session_id=session_id,
            adapter_key=None,
            parent_session_id=parent_session_id,
        )

    async def find_session_by_sender(self, *, adapter_key: str, sender_ref: dict[str, Any]) -> str | None:
        return await self.route_store.find_session_by_sender(adapter_key=adapter_key, sender_ref=sender_ref)

    async def upload_file(
        self,
        *,
        session_id: str,
        adapter_key: str,
        message_id: str,
        content: bytes,
        filename: str,
        mime_type: str | None,
    ) -> str:
        return await self.bridge.upload_file(
            session_id=session_id,
            adapter_key=adapter_key,
            message_id=message_id,
            content=content,
            filename=filename,
            mime_type=mime_type,
        )

    async def download_file(self, *, session_id: str, file_id: str) -> tuple[bytes, str | None, str | None]:
        return await self.bridge.download_file(session_id=session_id, file_id=file_id)

    async def dispatch_outbound(self, outbound: GatewayOutboundMessage) -> None:
        try:
            adapter_key, _sender_ref = await self.route_store.resolve(outbound.session_id)
        except ValueError as exc:
            logger.error("Outbound route resolution failed: %s", exc)
            raise
        adapter = self.adapters.get(adapter_key)
        if adapter is None:
            raise RuntimeError(f"Adapter not found: {adapter_key}")
        await adapter.send(outbound)

    def build_router(self) -> APIRouter:
        router = APIRouter()
        for adapter in self.adapters.values():
            router.include_router(adapter.build_router())
        return router


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fairyclaw.gateway.adapters.base import GatewayAdapter
