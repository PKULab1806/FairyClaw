# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Planner core module.

Implements single-step agent orchestration, including:
1. Build context and call the LLM;
2. Execute tools and persist operation traces;
3. Maintain visible tool boundaries and subtask barrier aggregation.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field

from fairyclaw.core.agent.constants import (
    SUB_SESSION_MARKER,
    TaskType,
)
from fairyclaw.core.agent.context.history_ir import ChatHistoryItem, SessionMessageBlock, SessionMessageRole, ToolCallRound
from fairyclaw.core.agent.context.turn_context_builder import TurnContextBuilder
from fairyclaw.core.agent.hooks.protocol import (
    AfterToolCallHookPayload,
    BeforeToolCallHookPayload,
    HookExecutionContext,
    HookStage,
    JsonObject,
    LlmFunctionToolSpec,
    LlmToolCallRequest,
    ToolsPreparedHookPayload,
    to_openai_messages,
)
from fairyclaw.core.agent.planning.subtask_coordinator import SubtaskCoordinator
from fairyclaw.core.agent.planning.tool_logging import make_short_tool_call_id, summarize_tool_args
from fairyclaw.core.agent.session.global_state import get_session_lock
from fairyclaw.core.agent.session.memory import PersistentMemory
from fairyclaw.core.events.runtime import get_user_gateway
from fairyclaw.core.runtime.session_runtime_store import get_session_runtime_store
from fairyclaw.core.agent.types import SessionKind, TurnRequest, TurnRuntimePrefs
from fairyclaw.core.domain import ContentSegment
from fairyclaw.core.events.bus import EventType
from fairyclaw.core.events.runtime import publish_runtime_event
from fairyclaw.infrastructure.llm.client import ChatResult

from .planner_core import BasePlanner
from .turn_policy import MainSessionTurnPolicy, SubSessionTurnPolicy, TurnExecutionPolicy

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ForceFinishDirective:
    """Describe one hook-requested short-circuit directive."""

    stage: str
    reason: str | None = None
    details: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class PreparedTurnResult:
    """Typed result produced by turn preparation before tool execution."""

    task_type: str
    resolved_groups: list[str]
    hook_context: HookExecutionContext
    tool_calls: list[LlmToolCallRequest]
    message_text: str | None
    usage_prompt_tokens: int | None = None
    usage_completion_tokens: int | None = None
    usage_total_tokens: int | None = None
    force_finish: ForceFinishDirective | None = None


@dataclass(frozen=True)
class ToolBatchExecutionResult:
    """Typed result produced by one tool-call batch execution."""

    called_tools: list[str]
    force_finish: ForceFinishDirective | None = None


class Planner(BasePlanner):
    """Session-level single-step planner for event-driven orchestration.

    This class performs one LLM decision cycle per wakeup, executes returned tool calls,
    persists operation traces, and publishes follow-up runtime events for subsequent steps.
    """

    def _next_turn_id(self) -> str:
        """Generate unique turn identifier for hook context."""
        return f"turn_{uuid.uuid4().hex[:8]}"

    def __init__(self) -> None:
        """Initialize planner dependencies.

        Returns:
            None
        """
        super().__init__()
        self.logger = logger
        self.subtasks = SubtaskCoordinator()
        self.context_builder = TurnContextBuilder(self.message_assembler)
        self.execution_policies: dict[SessionKind, TurnExecutionPolicy] = {
            SessionKind.MAIN: MainSessionTurnPolicy(),
            SessionKind.SUB: SubSessionTurnPolicy(),
        }

    async def process_turn(self, request: TurnRequest) -> None:
        """Execute one planner advancement cycle from typed turn request."""
        await self._process_turn_request(request)

    async def _process_turn_request(self, request: TurnRequest) -> None:
        """Execute one planner advancement cycle from typed turn request."""
        async with get_session_lock(request.session_id):
            await self._run_turn_with_policy(request, self._resolve_policy(request))

    def _resolve_policy(self, request: TurnRequest) -> TurnExecutionPolicy:
        """Resolve execution policy for one turn request."""
        return self.execution_policies[self._resolve_session_kind(request)]

    async def _run_turn_with_policy(self, request: TurnRequest, policy: TurnExecutionPolicy) -> None:
        """Run one turn using the session-kind-specific execution policy."""
        if await policy.should_skip(self, request):
            return
        try:
            turn_result = await self._prepare_turn(
                request=request,
                is_sub_session=policy.kind is SessionKind.SUB,
            )
            if turn_result.force_finish is not None:
                if turn_result.message_text:
                    await self._handle_text_fallback(
                        request.session_id,
                        turn_result.message_text,
                        request.memory,
                        usage_prompt_tokens=turn_result.usage_prompt_tokens,
                        usage_completion_tokens=turn_result.usage_completion_tokens,
                        usage_total_tokens=turn_result.usage_total_tokens,
                    )
                return
            if not turn_result.tool_calls:
                await policy.handle_text_response(
                    self,
                    request,
                    turn_result.message_text,
                    usage_prompt_tokens=turn_result.usage_prompt_tokens,
                    usage_completion_tokens=turn_result.usage_completion_tokens,
                    usage_total_tokens=turn_result.usage_total_tokens,
                )
                return
            tool_result = await self._process_tool_calls(
                request.session_id,
                turn_result.tool_calls,
                turn_result.message_text,
                request.memory,
                usage_prompt_tokens=turn_result.usage_prompt_tokens,
                usage_completion_tokens=turn_result.usage_completion_tokens,
                usage_total_tokens=turn_result.usage_total_tokens,
                hook_context=turn_result.hook_context,
                enabled_groups=turn_result.resolved_groups,
            )
            if tool_result.force_finish is not None:
                return
            await policy.handle_tool_follow_up(
                self,
                request,
                turn_result.task_type,
                turn_result.resolved_groups,
                tool_result.called_tools,
            )
        except Exception as exc:
            await policy.handle_failure(self, request, exc)

    async def _prepare_turn(
        self,
        request: TurnRequest,
        is_sub_session: bool,
    ) -> PreparedTurnResult:
        runtime_context = await get_session_runtime_store().get(request.session_id)
        history_items = list(request.history_items)
        if request.memory:
            history_items = await request.memory.get_history(request.session_id)

        task_type = request.runtime.task_type
        current_llm_client = self.resolve_llm_client(task_type)
        if task_type != TaskType.GENERAL.value:
            logger.info("Using '%s' LLM profile for sub-task.", task_type)

        resolved_groups = self._resolve_tools_for_session(
            session_id=request.session_id,
            selected_groups=request.runtime.enabled_groups,
        )
        tool_specs = self._build_tool_specs(resolved_groups)
        hook_context = HookExecutionContext(
            session_id=request.session_id,
            turn_id=self._next_turn_id(),
            task_type=task_type,
            is_sub_session=is_sub_session,
            enabled_groups=list(resolved_groups),
        )
        tools_stage_payload = ToolsPreparedHookPayload(
            session_id=request.session_id,
            task_type=task_type,
            is_sub_session=is_sub_session,
            enabled_groups=list(resolved_groups),
            tools=tool_specs,
        )
        tools_stage_output = await self.hook_stage_runner.run_stage(
            stage=HookStage.TOOLS_PREPARED,
            hook_context=hook_context,
            payload=tools_stage_payload,
            enabled_groups=resolved_groups,
        )
        if isinstance(tools_stage_output.patched_payload, ToolsPreparedHookPayload):
            tools_stage_payload = tools_stage_output.patched_payload
            resolved_groups = list(tools_stage_payload.enabled_groups)
            tool_specs = list(tools_stage_payload.tools)
            hook_context.enabled_groups = list(resolved_groups)

        messages, history_items, user_turn = self.context_builder.build(
            history_items=history_items,
            user_segments=request.user_segments,
            session_id=request.session_id,
            task_type=task_type,
            workspace_root=runtime_context.workspace_root,
        )
        always_enabled_groups = [
            name
            for name, group in self.registry.groups.items()
            if (group.always_enable_subagent if is_sub_session else group.always_enable_planner)
        ]
        before_llm_payload, hook_context = await self.context_pipeline.run(
            stage_runner=self.hook_stage_runner,
            turn_id_factory=lambda: hook_context.turn_id,
            always_enabled_groups=always_enabled_groups,
            registry_groups=self.registry.groups,
            session_id=request.session_id,
            messages=messages,
            tools=tool_specs,
            history_items=history_items,
            user_turn=user_turn,
            enabled_groups=resolved_groups,
            task_type=task_type,
            is_sub_session=is_sub_session,
        )
        before_llm_directive = self._extract_force_finish_directive(
            stage=HookStage.BEFORE_LLM_CALL.value,
            force_finish=before_llm_payload.force_finish,
            reason=before_llm_payload.force_finish_reason,
        )
        if before_llm_directive is not None:
            await self._publish_force_finish_event(
                session_id=request.session_id,
                hook_context=hook_context,
                enabled_groups=resolved_groups,
                directive=before_llm_directive,
            )
            return PreparedTurnResult(
                task_type=task_type,
                resolved_groups=list(resolved_groups),
                hook_context=hook_context,
                tool_calls=[],
                message_text=None,
                usage_prompt_tokens=None,
                usage_completion_tokens=None,
                usage_total_tokens=None,
                force_finish=before_llm_directive,
            )
        openai_messages = to_openai_messages(before_llm_payload.turn.llm_messages)
        openai_tools = [spec.to_openai_tool() for spec in before_llm_payload.tools]
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("LLM Prompt: %s", json.dumps(openai_messages, ensure_ascii=False, indent=2))
        chat_result = await current_llm_client.chat_with_tools(messages=openai_messages, tools=openai_tools)
        chat_result = await self._repair_length_truncated_tool_calls_if_needed(
            llm_client=current_llm_client,
            messages=openai_messages,
            tools=openai_tools,
            chat_result=chat_result,
        )
        # Normalize tool-call ids for stored history and the next LLM request. Provider ids are arbitrary;
        # the API only requires assistant tool_calls[].id and following tool.tool_call_id to match in our payload.
        llm_tool_calls = [
            LlmToolCallRequest(
                call_id=make_short_tool_call_id(call.id, index),
                name=call.name,
                arguments_json=call.arguments,
            )
            for index, call in enumerate(chat_result.tool_calls)
        ]
        after_llm_payload = await self.tool_pipeline.run_after_llm_response(
            stage_runner=self.hook_stage_runner,
            hook_context=hook_context,
            enabled_groups=resolved_groups,
            llm_response=chat_result,
            tool_calls=llm_tool_calls,
        )
        resolved_groups = list(after_llm_payload.enabled_groups)
        after_llm_directive = self._extract_force_finish_directive(
            stage=HookStage.AFTER_LLM_RESPONSE.value,
            force_finish=after_llm_payload.force_finish,
            reason=after_llm_payload.force_finish_reason,
        )
        if after_llm_directive is not None:
            await self._publish_force_finish_event(
                session_id=request.session_id,
                hook_context=hook_context,
                enabled_groups=resolved_groups,
                directive=after_llm_directive,
            )
        return PreparedTurnResult(
            task_type=task_type,
            resolved_groups=list(resolved_groups),
            hook_context=hook_context,
            tool_calls=list(after_llm_payload.tool_calls),
            message_text=after_llm_payload.message_text,
            usage_prompt_tokens=chat_result.prompt_tokens,
            usage_completion_tokens=chat_result.completion_tokens,
            usage_total_tokens=chat_result.total_tokens,
            force_finish=after_llm_directive,
        )

    def _has_invalid_tool_args_json(self, tool_calls: list[LlmToolCallRequest]) -> bool:
        """Return True when at least one tool call contains malformed JSON arguments."""
        for call in tool_calls:
            args = call.arguments_json
            if not isinstance(args, str):
                continue
            try:
                json.loads(args)
            except json.JSONDecodeError:
                return True
        return False

    def _needs_length_truncation_repair(self, chat_result: ChatResult, tool_calls: list[LlmToolCallRequest]) -> bool:
        """Detect truncated response that likely cut tool-call JSON."""
        finish_reason = (chat_result.finish_reason or "").strip().lower()
        if finish_reason != "length":
            return False
        if not tool_calls:
            return False
        return self._has_invalid_tool_args_json(tool_calls)

    async def _repair_length_truncated_tool_calls_if_needed(
        self,
        *,
        llm_client: object,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None,
        chat_result: ChatResult,
    ) -> ChatResult:
        """Retry once with a compact repair prompt when tool-call arguments are truncated."""
        provisional_calls = [
            LlmToolCallRequest(
                call_id=make_short_tool_call_id(call.id, index),
                name=call.name,
                arguments_json=call.arguments,
            )
            for index, call in enumerate(chat_result.tool_calls)
        ]
        if not self._needs_length_truncation_repair(chat_result, provisional_calls):
            return chat_result

        logger.warning(
            "Detected finish_reason=length with malformed tool JSON; retrying a compact repair turn."
        )
        repair_messages = list(messages)
        repair_messages.append(
            {
                "role": "system",
                "content": (
                    "Your previous response was truncated (finish_reason=length) and produced malformed "
                    "tool_call arguments JSON. Re-emit only the necessary tool_calls with complete, valid JSON "
                    "arguments. Keep output minimal and do not include extra prose."
                ),
            }
        )
        retry_result = await llm_client.chat_with_tools(messages=repair_messages, tools=tools)
        retry_calls = [
            LlmToolCallRequest(
                call_id=make_short_tool_call_id(call.id, index),
                name=call.name,
                arguments_json=call.arguments,
            )
            for index, call in enumerate(retry_result.tool_calls)
        ]
        if retry_calls and not self._has_invalid_tool_args_json(retry_calls):
            return retry_result
        logger.error(
            "Tool-call repair after finish_reason=length still returned malformed JSON; skipping tool execution."
        )
        return ChatResult(
            text=(
                "Model output was truncated by upstream token limit and tool arguments stayed malformed after auto-repair. "
                "Skipped tool execution this turn to avoid invalid-call loops. Please retry with a smaller output scope."
            ),
            tool_calls=[],
            prompt_tokens=retry_result.prompt_tokens,
            completion_tokens=retry_result.completion_tokens,
            total_tokens=retry_result.total_tokens,
            finish_reason=retry_result.finish_reason,
        )

    async def _run_main_session_turn(self, request: TurnRequest) -> None:
        """Run one turn for a main session."""
        await self._run_turn_with_policy(request, self.execution_policies[SessionKind.MAIN])

    async def _run_sub_session_turn(self, request: TurnRequest) -> None:
        """Run one turn for a sub-session."""
        await self._run_turn_with_policy(request, self.execution_policies[SessionKind.SUB])

    def _resolve_tools_for_session(self, session_id: str, selected_groups: list[str] | None = None) -> list[str]:
        """Resolve visible capability groups with main/sub-session constraints.

        Args:
            session_id (str): Current session identifier.
            selected_groups (list[str] | None): Routed group list from delegation payload.

        Returns:
            list[str]: Effective capability group names visible in this turn.
        """
        is_sub_session = self._resolve_session_kind_from_id(session_id) is SessionKind.SUB
        if not is_sub_session:
            return self.capability_resolver.resolve(None, is_sub_session=False)
        return self.capability_resolver.resolve(selected_groups, is_sub_session=True)

    def _build_tool_specs(self, enabled_groups: list[str]) -> list[LlmFunctionToolSpec]:
        """Build typed function tool specs from capability registry."""
        openai_tools = self.registry.get_openai_tools(group_names=enabled_groups)
        specs: list[LlmFunctionToolSpec] = []
        for tool in openai_tools:
            spec = LlmFunctionToolSpec.from_openai_tool(tool)
            if spec is not None:
                specs.append(spec)
        return specs

    def _resolve_session_kind_from_id(self, session_id: str) -> SessionKind:
        """Resolve session kind from session identifier shape."""
        return SessionKind.SUB if SUB_SESSION_MARKER in session_id else SessionKind.MAIN

    def _resolve_session_kind(self, request: TurnRequest) -> SessionKind:
        """Resolve session kind once per request."""
        if request.session_kind is not None:
            return request.session_kind
        return self._resolve_session_kind_from_id(request.session_id)

    def _should_publish_follow_up(self, called_tools: list[str]) -> bool:
        """Determine whether planner should enqueue internal follow-up.

        Args:
            called_tools (list[str]): Tool names executed in current turn.

        Returns:
            bool: True when at least one tool call executed.
        """
        return len(called_tools) > 0

    async def _publish_follow_up_event(self, session_id: str, task_type: str, enabled_groups: list[str]) -> None:
        """Publish one internal follow-up wakeup event."""
        await publish_runtime_event(
            event_type=EventType.USER_MESSAGE_RECEIVED,
            session_id=session_id,
            payload={
                "internal_followup": True,
                "trigger_turn": True,
                "task_type": task_type,
                "enabled_groups": enabled_groups,
            },
            source="planner_followup",
        )

    def _extract_force_finish_directive(
        self,
        stage: str,
        force_finish: bool,
        reason: str | None,
        details: JsonObject | None = None,
    ) -> ForceFinishDirective | None:
        """Build a typed short-circuit directive from hook payload fields."""
        if not force_finish:
            return None
        return ForceFinishDirective(stage=stage, reason=reason, details=dict(details or {}))

    async def _publish_force_finish_event(
        self,
        session_id: str,
        hook_context: HookExecutionContext,
        enabled_groups: list[str],
        directive: ForceFinishDirective,
    ) -> None:
        """Publish one non-triggering runtime event for hook-requested short-circuit."""
        await publish_runtime_event(
            event_type=EventType.FORCE_FINISH_REQUESTED,
            session_id=session_id,
            payload={
                "trigger_turn": False,
                "reason": directive.reason,
                "stage": directive.stage,
                "turn_id": hook_context.turn_id,
                "task_type": hook_context.task_type,
                "enabled_groups": list(enabled_groups),
                "is_sub_session": hook_context.is_sub_session,
                "details": directive.details,
            },
            source="planner_force_finish",
        )

    def _is_sub_session_terminal(self, sub_session_id: str) -> bool:
        """Check whether a sub-session is already terminal in main-session state."""
        return self.subtasks.is_sub_session_terminal(sub_session_id)

    async def _mark_subtask_if_non_terminal(self, sub_session_id: str, status: str, summary: str) -> None:
        """Mark subtask terminal only when it is not already terminal.

        Args:
            sub_session_id (str): Sub-session identifier.
            status (str): Target terminal status.
            summary (str): Terminal summary text.

        Returns:
            None
        """
        await self.subtasks.mark_subtask_if_non_terminal(sub_session_id, status, summary)

    def _lookup_subtask_status(self, sub_session_id: str) -> str | None:
        return self.subtasks.lookup_subtask_status(sub_session_id)

    async def _publish_subtask_barrier_if_ready(self, sub_session_id: str) -> None:
        """Publish aggregated barrier message when all subtasks are terminal.

        Args:
            sub_session_id (str): Any sub-session in the target main-session batch.

        Returns:
            None

        Raises:
            Exceptions during persistence or event publication are caught and logged.
        """
        await self.subtasks.publish_subtask_barrier_if_ready(sub_session_id)

    async def _try_publish_subtask_barrier(self, sub_session_id: str) -> None:
        """Compatibility wrapper for legacy subtask barrier helper name."""
        await self._publish_subtask_barrier_if_ready(sub_session_id)

    async def _notify_main_session_subtask_failure(self, sub_session_id: str, summary: str, status: str = "failed") -> None:
        await self.subtasks.notify_main_session_subtask_failure(sub_session_id, summary, status=status)

    async def _handle_text_fallback(
        self,
        session_id: str,
        content: str,
        memory: PersistentMemory | None,
        *,
        usage_prompt_tokens: int | None = None,
        usage_completion_tokens: int | None = None,
        usage_total_tokens: int | None = None,
    ) -> None:
        """Handle direct-text model output path.

        Args:
            session_id (str): Session identifier.
            content (str): Model-generated text.
            memory (PersistentMemory | None): Memory adapter for session persistence.

        Returns:
            None
        """
        if memory:
            assistant_message = SessionMessageBlock.from_segments(
                SessionMessageRole.ASSISTANT,
                (ContentSegment.text_segment(content),),
            )
            if assistant_message is not None:
                await memory.add_session_event(
                    session_id=session_id,
                    message=assistant_message,
                    usage_prompt_tokens=usage_prompt_tokens,
                    usage_completion_tokens=usage_completion_tokens,
                    usage_total_tokens=usage_total_tokens,
                )
                gw = get_user_gateway()
                if gw:
                    await gw.emit_assistant_text(session_id, content)

    async def _process_tool_calls(
        self,
        session_id: str,
        tool_calls: list[LlmToolCallRequest],
        message_text: str | None,
        memory: PersistentMemory | None,
        *,
        usage_prompt_tokens: int | None = None,
        usage_completion_tokens: int | None = None,
        usage_total_tokens: int | None = None,
        hook_context: HookExecutionContext | None = None,
        enabled_groups: list[str] | None = None,
    ) -> ToolBatchExecutionResult:
        """Execute model-returned tool calls with before/after-tool hooks.

        Args:
            session_id (str): Session identifier.
            tool_calls (list[LlmToolCallRequest]): Parsed tool calls to execute.
            message_text (str | None): Optional model text returned with tool calls.
            memory (PersistentMemory | None): Memory adapter for operation persistence.

        Returns:
            ToolBatchExecutionResult: Structured tool-batch result and optional short-circuit directive.

        Raises:
            Individual tool exceptions are converted to error strings and persisted as operation results.
        """
        visible_message = self._normalize_assistant_tool_message(message_text)
        if visible_message:
            if memory:
                assistant_preface = SessionMessageBlock.from_segments(
                    SessionMessageRole.ASSISTANT,
                    (ContentSegment.text_segment(visible_message),),
                )
                if assistant_preface is not None:
                    await memory.add_session_event(
                        session_id=session_id,
                        message=assistant_preface,
                        usage_prompt_tokens=usage_prompt_tokens,
                        usage_completion_tokens=usage_completion_tokens,
                        usage_total_tokens=usage_total_tokens,
                    )
                    gw = get_user_gateway()
                    if gw:
                        await gw.emit_assistant_text(session_id, visible_message)
                usage_prompt_tokens = None
                usage_completion_tokens = None
                usage_total_tokens = None

        called_tools: list[str] = []
        for index, raw_request in enumerate(tool_calls):
            request = raw_request
            if hook_context is not None:
                before_output = await self.hook_stage_runner.run_stage(
                    stage=HookStage.BEFORE_TOOL_CALL,
                    hook_context=hook_context,
                    payload=BeforeToolCallHookPayload(
                        request=request,
                        session_id=session_id,
                        call_index=index,
                        enabled_groups=list(enabled_groups or []),
                    ),
                    enabled_groups=enabled_groups or [],
                )
                before_payload = (
                    before_output.patched_payload
                    if isinstance(before_output.patched_payload, BeforeToolCallHookPayload)
                    else BeforeToolCallHookPayload(
                        request=request,
                        session_id=session_id,
                        call_index=index,
                        enabled_groups=list(enabled_groups or []),
                    )
                )
                request = before_payload.request
                before_directive = self._extract_force_finish_directive(
                    stage=HookStage.BEFORE_TOOL_CALL.value,
                    force_finish=before_payload.force_finish,
                    reason=before_payload.force_finish_reason,
                    details={
                        "call_index": index,
                        "tool_name": request.name,
                        "call_id": request.call_id,
                    },
                )
                if before_directive is not None:
                    await self._publish_force_finish_event(
                        session_id=session_id,
                        hook_context=hook_context,
                        enabled_groups=list(enabled_groups or []),
                        directive=before_directive,
                    )
                    return ToolBatchExecutionResult(
                        called_tools=list(called_tools),
                        force_finish=before_directive,
                    )

            func_name = request.name
            args_json = request.arguments_json
            call_id = request.call_id or make_short_tool_call_id("", index)
            args_summary = summarize_tool_args(func_name, args_json)
            logger.info(
                f"Tool call requested: session={session_id}, tool={func_name}, call_id={call_id}, args={args_summary}"
            )
            gw = get_user_gateway()
            if memory and gw:
                await gw.emit_tool_call(
                    session_id,
                    tool_call_id=call_id,
                    tool_name=func_name,
                    arguments_json=args_json,
                )
            try:
                tool_result_raw = await self.tool_runtime.execute(
                    tool_name=func_name,
                    arguments_json=args_json,
                    session_id=session_id,
                    memory=memory,
                    planner=self,
                )
                tool_status = "ok"
                result_text = "" if tool_result_raw is None else str(tool_result_raw)
                logger.info(f"Tool call finished: session={session_id}, tool={func_name}, call_id={call_id}")
            except Exception as exc:
                logger.error(f"Tool {func_name} failed: {exc}")
                tool_status = "error"
                result_text = f"Error executing tool {func_name}: {str(exc)}"

            tool_def = self.registry.tools.get(func_name)
            should_record = True
            if tool_def and not tool_def.record_event:
                should_record = False

            if should_record and memory:
                tr = ToolCallRound(
                    tool_name=func_name,
                    call_id=call_id,
                    arguments_json=args_json,
                    tool_result=result_text,
                    success=(tool_status == "ok"),
                )
                if usage_total_tokens is not None or usage_prompt_tokens is not None or usage_completion_tokens is not None:
                    await memory.add_operation_event(
                        session_id=session_id,
                        tool_round=tr,
                        usage_prompt_tokens=usage_prompt_tokens,
                        usage_completion_tokens=usage_completion_tokens,
                        usage_total_tokens=usage_total_tokens,
                    )
                    usage_prompt_tokens = None
                    usage_completion_tokens = None
                    usage_total_tokens = None
                else:
                    await memory.add_operation_event(session_id=session_id, tool_round=tr)
                gw2 = get_user_gateway()
                if gw2:
                    await gw2.emit_tool_result(session_id, tr)

            if hook_context is not None:
                after_output = await self.hook_stage_runner.run_stage(
                    stage=HookStage.AFTER_TOOL_CALL,
                    hook_context=hook_context,
                    payload=AfterToolCallHookPayload(
                        request=request,
                        session_id=session_id,
                        call_index=index,
                        result=result_text,
                        tool_status=tool_status,
                    ),
                    enabled_groups=enabled_groups or [],
                )
                after_payload = (
                    after_output.patched_payload
                    if isinstance(after_output.patched_payload, AfterToolCallHookPayload)
                    else AfterToolCallHookPayload(
                        request=request,
                        session_id=session_id,
                        call_index=index,
                        result=result_text,
                        tool_status=tool_status,
                    )
                )
                result_text = after_payload.result
                tool_status = after_payload.tool_status
                after_directive = self._extract_force_finish_directive(
                    stage=HookStage.AFTER_TOOL_CALL.value,
                    force_finish=after_payload.force_finish,
                    reason=after_payload.force_finish_reason,
                    details={
                        "call_index": index,
                        "tool_name": request.name,
                        "call_id": request.call_id,
                    },
                )
                if after_directive is not None:
                    called_tools.append(func_name)
                    await self._publish_force_finish_event(
                        session_id=session_id,
                        hook_context=hook_context,
                        enabled_groups=list(enabled_groups or []),
                        directive=after_directive,
                    )
                    return ToolBatchExecutionResult(
                        called_tools=list(called_tools),
                        force_finish=after_directive,
                    )

            called_tools.append(func_name)

        return ToolBatchExecutionResult(called_tools=called_tools)

    def _normalize_assistant_tool_message(self, message_text: str | None) -> str | None:
        """Normalize assistant text that accompanies tool calls."""
        if not message_text:
            return None
        text = message_text.strip()
        if text.startswith("<thought>") and text.endswith("</thought>"):
            text = text[9:-10].strip()
        return text or None
