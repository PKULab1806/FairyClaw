# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Constants and enums shared by agent orchestration modules."""

from __future__ import annotations

from enum import Enum


class TaskType(str, Enum):
    """Supported task types for LLM profile selection."""

    GENERAL = "general"
    IMAGE = "image"
    CODE = "code"


SUB_SESSION_MARKER = "_sub_"

SYSTEM_NOTIFICATION_COMPLETED_PREFIX = "[System Notification] Background tasks completed:\n\n"
SYSTEM_NOTIFICATION_FAILED_PREFIX = "[System Notification] Sub-agent failed early:\n\n"
