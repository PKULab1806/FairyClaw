# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Backward-compatible re-exports for the bridge WebSocket surface."""

from fairyclaw.bridge.user_gateway import UserGateway, create_ws_bridge_router

__all__ = ["UserGateway", "create_ws_bridge_router"]
