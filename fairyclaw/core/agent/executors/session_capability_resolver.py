# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Capability resolution executor for session scope."""

from __future__ import annotations

from typing import List

from fairyclaw.core.capabilities.registry import CapabilityRegistry


class SessionCapabilityResolver:
    """Resolve enabled capability groups for planner lifecycle."""

    def __init__(self, registry: CapabilityRegistry) -> None:
        self.registry = registry

    def resolve(self, selected_groups: list[str] | None, is_sub_session: bool) -> List[str]:
        """Resolve enabled groups with scope-aware always-enable semantics."""
        return self.registry.resolve_enabled_groups(selected_groups, is_sub_session=is_sub_session)
