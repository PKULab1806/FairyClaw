# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Base gateway adapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from fastapi import APIRouter

from fairyclaw.core.gateway_protocol.models import GatewayAdapterDescriptor, GatewayOutboundMessage


class GatewayAdapter(ABC):
    """Base class for gateway adapters."""

    adapter_key: str
    kind: str
    version: str = "1"

    def build_router(self) -> APIRouter:
        """Return adapter HTTP router when the adapter exposes endpoints."""
        return APIRouter()

    def descriptor(self) -> GatewayAdapterDescriptor:
        """Return adapter descriptor advertised in hello payload."""
        return GatewayAdapterDescriptor(
            adapter_key=self.adapter_key,
            kind=self.kind,
            version=self.version,
        )

    async def start(self, runtime: "GatewayRuntime") -> None:
        """Attach runtime on startup."""
        self.runtime = runtime

    async def stop(self) -> None:
        """Stop adapter resources."""

    @abstractmethod
    async def send(self, outbound: GatewayOutboundMessage) -> None:
        """Send one outbound message to the external platform."""


from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fairyclaw.gateway.runtime import GatewayRuntime
