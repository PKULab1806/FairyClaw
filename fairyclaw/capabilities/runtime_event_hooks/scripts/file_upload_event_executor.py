# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Default plugin executor for file upload received events."""

from __future__ import annotations

import logging

from fairyclaw.sdk.events import EventType, FileUploadReceivedEventPayload
from fairyclaw.sdk.hooks import EventHookHandler, HookExecutionContext, HookStageOutput, HookStatus

logger = logging.getLogger(__name__)


class FileUploadEventExecutor(EventHookHandler):
    """No-op event handler for uploaded files."""

    event_type = EventType.FILE_UPLOAD_RECEIVED

    async def run(
        self,
        payload: FileUploadReceivedEventPayload,
        ctx: HookExecutionContext,
    ) -> HookStageOutput[FileUploadReceivedEventPayload]:
        logger.info(
            "file_upload_event_executor noop consume event: session=%s file_id=%s",
            ctx.session_id,
            payload.file_id,
        )
        return HookStageOutput(
            status=HookStatus.SKIP,
            patched_payload=payload,
            artifacts={"event_type": EventType.FILE_UPLOAD_RECEIVED.value, "handled_by": "file_upload_event_executor"},
        )
