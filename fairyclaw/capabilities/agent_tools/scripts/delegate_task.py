# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import base64
import logging
import mimetypes
import os
import re
import time
import uuid
from typing import Any, Dict

from fairyclaw.core.agent.context.history_ir import SessionMessageBlock, SessionMessageRole
from fairyclaw.core.capabilities.models import ToolContext
from fairyclaw.core.domain import ContentSegment
from fairyclaw.core.events.bus import EventType
from fairyclaw.core.events.runtime import get_user_gateway, publish_runtime_event
from fairyclaw.core.runtime.session_runtime_store import get_session_runtime_store
from fairyclaw.core.agent.session.global_state import bind_sub_session, get_or_create_subtask_state
from fairyclaw.core.agent.session.memory import PersistentMemory
from fairyclaw.infrastructure.database.models import FileModel, GatewaySessionRouteModel, SessionModel
from fairyclaw.infrastructure.database.repository import EventRepository, FileRepository
from fairyclaw.infrastructure.database.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

# New uploads use file_ + 12 hex; legacy may be up to 32. Reject `file_id_…` and other garbage.
SESSION_FILE_ID_RE = re.compile(r"^file_[0-9a-fA-F]{8,32}$")


def _make_sub_session_id(parent_session_id: str) -> str:
    """Generate bounded-length sub-session ID from parent session.

    Args:
        parent_session_id (str): Parent session identifier.

    Returns:
        str: New sub-session ID with ``_sub_`` marker and random suffix.
    """
    parent_prefix = parent_session_id if "_sub_" in parent_session_id else parent_session_id[:10]
    if len(parent_prefix) > 28:
        parent_prefix = parent_prefix[-28:]
    return f"{parent_prefix}_sub_{uuid.uuid4().hex[:6]}"


async def _session_ids_for_file_lookup(file_repo: FileRepository, owning_session_id: str) -> list[str]:
    """Own session first, then parent session (attachments often live on the main session)."""
    ids: list[str] = [owning_session_id]
    model = await file_repo.db.get(SessionModel, owning_session_id)
    if model is None or not isinstance(model.meta, dict):
        return ids
    parent = model.meta.get("parent_session_id")
    if isinstance(parent, str) and parent.strip() and parent.strip() not in ids:
        ids.append(parent.strip())
    return ids


async def _get_owned_session_file(
    file_repo: FileRepository,
    file_id: str,
    owning_session_id: str,
) -> FileModel | None:
    """Resolve session file by exact id; row must belong to owning or parent session."""
    raw = (file_id or "").strip()
    if not raw.startswith("file_"):
        return None
    model = await file_repo.get(raw)
    if model is None:
        return None
    for sid in await _session_ids_for_file_lookup(file_repo, owning_session_id):
        if model.session_id == sid:
            return model
    return None


async def _validate_attachments_for_delegation(
    file_repo: FileRepository,
    attachments: list[str],
    owning_session_id: str,
) -> str | None:
    """Return an error string if any attachment is unusable; otherwise None."""
    for att in attachments:
        if att.startswith(("http://", "https://")):
            continue
        if os.path.exists(att):
            continue
        if att.startswith("file_"):
            if not SESSION_FILE_ID_RE.fullmatch(att):
                return (
                    "Error: Task delegation failed. Invalid session file id in attachments: "
                    f"{att!r}. Expected `file_` plus 8–32 hexadecimal characters only (copy exactly from "
                    "list_session_files or the upload response). Do not use `file_id_…`, UUID-style words, "
                    "or invented ids."
                )
            model = await _get_owned_session_file(file_repo, att, owning_session_id)
            if model is None:
                return (
                    "Error: Task delegation failed. No session file exists for attachment "
                    f"{att!r}. Call list_session_files and pass the exact file id shown there; do not guess."
                )
            continue
        return (
            "Error: Task delegation failed. Unrecognized attachment "
            f"{att!r}: not an http(s) URL, not a path that exists on the server, and not a valid session file id."
        )
    return None


async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    """Create and start a delegated sub-agent task.

    Args:
        args (Dict[str, Any]): Delegation payload including instruction/background/task_type/attachments.
        context (ToolContext): Runtime context containing parent session and planner references.

    Returns:
        str: Human-readable startup result containing sub-session ID.

    Raises:
        This function converts most operational failures into error strings and avoids raising business
        exceptions outward so planner loop can continue.
    """
    instruction = str(args.get("instruction") or "")
    background = args.get("background", "")
    task_type = args.get("task_type", "general")
    raw_attachments = args.get("attachments", [])
    if not isinstance(raw_attachments, list):
        raw_attachments = []
    attachments = [str(a).strip() for a in raw_attachments if str(a).strip()]
    if not instruction:
        return "Error: instruction is required."

    if not context.planner:
        return "Error: Planner instance not available in context. Cannot spawn sub-agent."

    depth = context.session_id.count("_sub_")
    if depth >= 2:
        return "Error: Maximum Sub-Agent nesting depth reached. You must complete this task yourself without delegating further."

    main_session_id = context.session_id
    inherited_workspace_root = ""
    if context.runtime_context is not None and context.runtime_context.workspace_root:
        inherited_workspace_root = str(context.runtime_context.workspace_root).strip()
    if not inherited_workspace_root:
        try:
            inherited_workspace_root = (await get_session_runtime_store().get(main_session_id)).workspace_root
        except Exception:
            inherited_workspace_root = ""

    if attachments:
        try:
            async with AsyncSessionLocal() as vdb:
                vrepo = FileRepository(vdb)
                err = await _validate_attachments_for_delegation(vrepo, attachments, main_session_id)
                if err:
                    return err
        except Exception as e:
            return f"Error: Task delegation failed while validating attachments: {e}"

    sub_session_id = _make_sub_session_id(main_session_id)
    short_instruction = instruction.replace("\n", " ").strip()
    if len(short_instruction) > 80:
        short_instruction = short_instruction[:80] + "…"

    try:
        async with AsyncSessionLocal() as local_db:
            sub_session_model = SessionModel(
                id=sub_session_id,
                platform="sub_agent",
                title=f"{task_type} | {short_instruction}",
                meta={
                    "parent_session_id": main_session_id,
                    "workspace_root": inherited_workspace_root,
                    "task_type": str(task_type),
                    "instruction": instruction,
                    "subtask_status": f"running:{task_type}",
                },
            )
            local_db.add(sub_session_model)
            local_db.add(
                GatewaySessionRouteModel(
                    session_id=sub_session_id,
                    adapter_key=None,
                    sender_ref={},
                    sender_platform=None,
                    sender_user_id=None,
                    sender_group_id=None,
                    sender_self_id=None,
                    parent_session_id=main_session_id,
                )
            )
            await local_db.commit()
    except Exception as e:
        return f"Error: Failed to initialize sub-session in database: {e}"

    full_instruction = instruction
    if background:
        full_instruction = f"Background: {background}\n\nTask: {instruction}"

    user_segments: list[ContentSegment] = [ContentSegment.text_segment(full_instruction)]

    if attachments:
        try:
            async with AsyncSessionLocal() as local_db:
                file_repo = FileRepository(local_db)
                for att in attachments:
                    if os.path.exists(att):
                        mime_type, _ = mimetypes.guess_type(att)
                        if mime_type and mime_type.startswith("image"):
                            try:
                                with open(att, "rb") as binary_file:
                                    b64_data = base64.b64encode(binary_file.read()).decode("utf-8")
                                user_segments.append(ContentSegment.image_url_segment(f"data:{mime_type};base64,{b64_data}"))
                            except Exception as e:
                                user_segments.append(ContentSegment.text_segment(f"\n\n[Failed to attach image {att}: {e}]"))
                        else:
                            try:
                                with open(att, "r", encoding="utf-8") as text_file:
                                    content = text_file.read()
                                user_segments.append(ContentSegment.text_segment(f"\n\nAttachment ({att}):\n{content}"))
                            except Exception as e:
                                user_segments.append(ContentSegment.text_segment(f"\n\n[Failed to read attachment {att}: {e}]"))
                    elif att.startswith(("http://", "https://")):
                        user_segments.append(ContentSegment.image_url_segment(att))
                    elif att.startswith("file_"):
                        file_model = await _get_owned_session_file(file_repo, att, main_session_id)
                        if file_model is None:
                            return (
                                "Error: Task delegation failed. Session file disappeared after validation for "
                                f"{att!r}. Retry delegation."
                            )
                        new_file = await file_repo.create(
                            session_id=sub_session_id,
                            filename=file_model.filename,
                            content=file_model.content,
                            mime_type=file_model.mime_type,
                        )

                        mime_type = new_file.mime_type or "application/octet-stream"
                        if not mime_type.startswith("image"):
                            try:
                                import filetype  # type: ignore[import-untyped]

                                kind = filetype.guess(new_file.content)
                                if kind and kind.mime.startswith("image"):
                                    mime_type = kind.mime
                            except ImportError:
                                pass

                        if mime_type.startswith("image"):
                            b64_data = base64.b64encode(new_file.content).decode("utf-8")
                            user_segments.append(ContentSegment.image_url_segment(f"data:{mime_type};base64,{b64_data}"))
                            user_segments.append(
                                ContentSegment.text_segment(
                                    f"\n\n[Attached Image File: {new_file.filename} (ID: {new_file.id})]"
                                )
                            )
                        else:
                            try:
                                text_content = new_file.content.decode("utf-8")
                                user_segments.append(
                                    ContentSegment.text_segment(
                                        f"\n\nAttachment ({new_file.filename}, ID: {new_file.id}):\n{text_content}"
                                    )
                                )
                            except UnicodeDecodeError:
                                user_segments.append(
                                    ContentSegment.text_segment(
                                        f"\n\n[Attached Binary File: {new_file.filename} (ID: {new_file.id}). "
                                        "Cannot be read as text.]"
                                    )
                                )
                    else:
                        return f"Error: Task delegation failed. Internal error for attachment {att!r}."
        except Exception as e:
            return f"Error: Failed to build attachments for sub-session: {e}"

    route_input = full_instruction.strip()
    if attachments:
        route_input += "\n\nAttachments: " + ", ".join(str(item) for item in attachments)
    selected_groups = context.planner.registry.resolve_enabled_groups(is_sub_session=True)
    try:
        async with AsyncSessionLocal() as local_db:
            session_model = await local_db.get(SessionModel, sub_session_id)
            if session_model:
                meta = dict(session_model.meta or {})
                meta["enabled_groups"] = list(selected_groups)
                meta["routing_pending"] = True
                meta["route_input"] = route_input
                session_model.meta = meta
                await local_db.commit()
    except Exception as e:
        logger.warning("Failed to persist routing metadata for sub-session %s: %s", sub_session_id, e)

    try:
        async with AsyncSessionLocal() as db:
            repo = EventRepository(db)
            sub_memory = PersistentMemory(repo)
            initial_message = SessionMessageBlock.from_segments(SessionMessageRole.USER, tuple(user_segments))
            if initial_message is None:
                return "Error: Failed to build initial sub-session message."
            await sub_memory.add_session_event(
                session_id=sub_session_id,
                message=initial_message,
            )
    except Exception as e:
        return f"Error: Failed to initialize sub-session history: {e}"

    state = get_or_create_subtask_state(main_session_id)
    state.register_task(sub_session_id, instruction, time.time())
    bind_sub_session(main_session_id, sub_session_id)
    state.update_status(sub_session_id, f"running:{task_type}")
    uwg = get_user_gateway()
    if uwg is not None:
        await uwg.emit_subagent_tasks_snapshot(main_session_id)
    logger.info(
        "Sub-agent startup requested: main_session=%s, sub_session=%s, task_type=%s, selected_groups=%s",
        main_session_id,
        sub_session_id,
        task_type,
        selected_groups,
    )

    published = await publish_runtime_event(
        event_type=EventType.USER_MESSAGE_RECEIVED,
        session_id=sub_session_id,
        payload={
            "segment_count": len(user_segments),
            "trigger_turn": True,
            "task_type": task_type,
            "parent_session_id": main_session_id,
            "selected_groups": selected_groups,
            "enabled_groups": selected_groups,
        },
        source="delegate_task",
    )
    if not published:
        state.mark_terminal(sub_session_id, "failed", "Runtime bus unavailable for sub-task wakeup.")
        try:
            async with AsyncSessionLocal() as db:
                sub_session = await db.get(SessionModel, sub_session_id)
                if sub_session and isinstance(sub_session.meta, dict):
                    meta = dict(sub_session.meta)
                    meta["subtask_status"] = "failed"
                    sub_session.meta = meta
                    await db.commit()
        except Exception as e:
            logger.warning("Failed to persist failed subtask status for %s: %s", sub_session_id, e)
        logger.error(
            "Sub-agent startup failed: main_session=%s, sub_session=%s, reason=runtime_bus_unavailable",
            main_session_id,
            sub_session_id,
        )
        return f"Task delegated but failed to start. Sub-agent Session ID: {sub_session_id}."
    logger.info("Sub-agent started: main_session=%s, sub_session=%s", main_session_id, sub_session_id)
    return (
        f"Task delegated successfully. Sub-agent started with Session ID: {sub_session_id}. "
        "You will receive a [System Notification] message when it completes."
    )
