# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Hook runtime protocol models."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Generic, TypeAlias, TypeVar

from fairyclaw.core.agent.context.history_ir import ChatHistoryItem, UserTurn

if TYPE_CHECKING:
    from fairyclaw.infrastructure.llm.client import LlmModelResponse

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]
PayloadT = TypeVar("PayloadT")


class HookStage(str, Enum):
    """Supported hook lifecycle stages."""

    TOOLS_PREPARED = "tools_prepared"
    BEFORE_LLM_CALL = "before_llm_call"
    AFTER_LLM_RESPONSE = "after_llm_response"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"


class HookStatus(str, Enum):
    """Hook execution status."""

    OK = "ok"
    SKIP = "skip"
    ERROR = "error"


@dataclass
class HookExecutionContext:
    """Shared execution metadata passed to every hook stage.

    Attributes:
        session_id: Stable session identifier for the current planner run.
        turn_id: Unique identifier for the current planner turn or event-dispatch turn.
        task_type: Logical LLM profile / task class chosen for this turn.
        is_sub_session: Whether this turn belongs to a delegated sub-session.
        enabled_groups: Capability groups visible to the current turn after routing/resolution.
        always_enabled_groups: Capability groups that are forced on by role policy.
        llm_profile: Concrete LLM profile name used by the runtime.
        token_budget: Optional token budget exposed to hooks for context shaping.
        time_budget_ms: Optional wall-clock budget exposed to hooks.
        metadata: Extra typed execution metadata that is not stage-payload-specific.
    """

    session_id: str
    turn_id: str
    task_type: str
    is_sub_session: bool
    enabled_groups: list[str] = field(default_factory=list)
    always_enabled_groups: list[str] = field(default_factory=list)
    llm_profile: str = "general"
    token_budget: int | None = None
    time_budget_ms: int | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass
class HookStageInput(Generic[PayloadT]):
    """Typed input envelope for one hook stage.

    Attributes:
        stage: Lifecycle stage currently being executed.
        context: Shared execution metadata for this stage invocation.
        payload: Stage-specific typed object. This is the object that must flow
            through the same-stage hook pipeline without degrading into `dict`.
        budget: Optional structured budget information reserved for hook runtime use.
        metadata: Optional structured per-invocation metadata reserved for hook runtime use.
    """

    stage: HookStage | str
    context: HookExecutionContext
    payload: PayloadT
    budget: JsonObject = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)


@dataclass
class HookError:
    """Normalized hook error structure."""

    code: str
    message: str
    retriable: bool = False


@dataclass
class HookStageOutput(Generic[PayloadT]):
    """Typed output envelope for one hook stage.

    Attributes:
        status: Hook execution result.
        patched_payload: Replacement payload for the next hook in the same stage.
            It must be the same payload type as the stage input.
        artifacts: Non-authoritative side-channel data emitted by hooks.
        metrics: Structured runtime metrics emitted by hooks.
        error: Normalized error payload when the hook runtime reports failure.
    """

    status: HookStatus
    patched_payload: PayloadT | None = None
    artifacts: JsonObject = field(default_factory=dict)
    metrics: JsonObject = field(default_factory=dict)
    error: HookError | None = None


@dataclass(frozen=True)
class LlmFunctionToolSpec:
    """Strongly typed tool schema visible to hooks before provider serialization.

    Attributes:
        name: Tool name exposed to the model.
        description: Human-readable description passed to the model.
        parameters: JSON-schema-like parameter definition for the tool.
    """

    name: str
    description: str
    parameters: JsonObject

    @classmethod
    def from_openai_tool(cls, tool: dict[str, object]) -> "LlmFunctionToolSpec | None":
        """Build typed tool spec from OpenAI-style tool dict."""
        function_raw = tool.get("function")
        if not isinstance(function_raw, dict):
            return None
        name = function_raw.get("name")
        description = function_raw.get("description")
        parameters = function_raw.get("parameters")
        if not isinstance(name, str) or not name.strip():
            return None
        if not isinstance(description, str):
            description = ""
        if not isinstance(parameters, dict):
            parameters = {}
        return cls(name=name, description=description, parameters=parameters)

    def to_openai_tool(self) -> dict[str, object]:
        """Serialize typed spec into OpenAI tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class LlmToolCallRequest:
    """One concrete tool call requested by the model.

    Attributes:
        call_id: Stable tool-call identifier (normalized in the planner; matches transcript tool_call_id).
        name: Requested tool name.
        arguments_json: Raw JSON argument string emitted by the model.
    """

    call_id: str
    name: str
    arguments_json: str

    def to_openai_tool_call(self) -> dict[str, object]:
        """Serialize tool call into assistant.tool_calls item."""
        return {
            "id": self.call_id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments_json},
        }


@dataclass(frozen=True)
class LlmChatMessage:
    """One provider-facing chat message before final serialization.

    This is an LLM boundary object, not business/history IR.

    Attributes:
        role: Chat role understood by the target LLM provider.
        content: Provider-facing message content.
        tool_calls: Tool calls attached to an assistant message.
        tool_call_id: Tool-call identifier for a tool result message.
        name: Optional tool name for tool-role messages.
    """

    role: str
    content: str | list[JsonObject] | None = None
    tool_calls: list[LlmToolCallRequest] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_openai_message(self) -> dict[str, object]:
        """Convert message into OpenAI-compatible dict."""
        payload: dict[str, object] = {"role": self.role}
        if self.content is not None:
            payload["content"] = self.content
        if self.tool_calls:
            payload["tool_calls"] = [call.to_openai_tool_call() for call in self.tool_calls]
            if "content" not in payload:
                payload["content"] = ""
        if self.tool_call_id:
            payload["tool_call_id"] = self.tool_call_id
        if self.name:
            payload["name"] = self.name
        return payload


def to_openai_messages(messages: list[LlmChatMessage]) -> list[dict[str, object]]:
    """Serialize typed chat messages into provider boundary payload."""
    return [message.to_openai_message() for message in messages]


@dataclass(frozen=True)
class LlmTurnContext:
    """Turn-scoped hook context spanning IR and provider-boundary data.

    Attributes:
        llm_messages: Provider-facing message list derived from the IR and ready
            for final serialization. Hooks may rewrite this when they need to
            influence the actual request sent to the model.
        history_items: Full typed conversation history IR reconstructed from
            session storage. This is the authoritative historical record hooks
            should read when they need semantic conversation context.
        user_turn: Current user turn IR for this planner cycle, if any.
        session_id: Session identifier owning this turn.
        task_type: Logical task/profile classification for the turn.
        is_sub_session: Whether this turn belongs to a delegated sub-session.
    """

    llm_messages: list[LlmChatMessage]
    history_items: list[ChatHistoryItem]
    user_turn: UserTurn | None
    session_id: str
    task_type: str
    is_sub_session: bool


@dataclass(frozen=True)
class ToolsPreparedHookPayload:
    """Stage payload for `tools_prepared`.

    Attributes:
        session_id: Session identifier owning this turn.
        task_type: Logical task/profile classification for the turn.
        is_sub_session: Whether this turn belongs to a delegated sub-session.
        enabled_groups: Capability groups currently enabled for the turn.
        tools: Typed tool schemas that will be exposed to the model.
    """

    session_id: str
    task_type: str
    is_sub_session: bool
    enabled_groups: list[str]
    tools: list[LlmFunctionToolSpec]


@dataclass(frozen=True)
class BeforeLlmCallHookPayload:
    """Stage payload for `before_llm_call`.

    Attributes:
        turn: Full turn context combining authoritative history IR and derived
            provider-facing request messages.
        tools: Typed tool schemas that will accompany the request.
        token_budget: Optional token budget exposed to hooks for context shaping.
        force_finish: Whether the hook wants to short-circuit the current turn
            immediately after this stage finishes.
        force_finish_reason: Optional structured reason describing why the hook
            requested the short-circuit.
    """

    turn: LlmTurnContext
    tools: list[LlmFunctionToolSpec]
    token_budget: int | None
    force_finish: bool = False
    force_finish_reason: str | None = None


@dataclass(frozen=True)
class AfterLlmResponseHookPayload:
    """Stage payload for `after_llm_response`.

    Attributes:
        session_id: Session identifier owning this turn.
        task_type: Logical task/profile classification for the turn.
        is_sub_session: Whether this turn belongs to a delegated sub-session.
        enabled_groups: Capability groups still enabled after the model response.
        message_text: Assistant text returned alongside tool calls, if any.
        tool_calls: Typed tool calls requested by the model.
        raw_llm_result: Raw normalized provider result for advanced hooks.
        force_finish: Whether the hook wants to short-circuit the current turn
            immediately after this stage finishes.
        force_finish_reason: Optional structured reason describing why the hook
            requested the short-circuit.
    """

    session_id: str
    task_type: str
    is_sub_session: bool
    enabled_groups: list[str]
    message_text: str | None
    tool_calls: list[LlmToolCallRequest]
    raw_llm_result: LlmModelResponse | None
    force_finish: bool = False
    force_finish_reason: str | None = None


@dataclass(frozen=True)
class BeforeToolCallHookPayload:
    """Stage payload for `before_tool_call`.

    Attributes:
        request: The concrete tool call about to be executed.
        session_id: Session identifier owning this turn.
        call_index: Position of this tool call within the current tool-call batch.
        enabled_groups: Capability groups enabled for this tool execution.
        force_finish: Whether the hook wants to stop the remaining tool batch and
            short-circuit the current turn before the tool is executed.
        force_finish_reason: Optional structured reason describing why the hook
            requested the short-circuit.
    """

    request: LlmToolCallRequest
    session_id: str
    call_index: int
    enabled_groups: list[str]
    force_finish: bool = False
    force_finish_reason: str | None = None


@dataclass(frozen=True)
class AfterToolCallHookPayload:
    """Stage payload for `after_tool_call`.

    Attributes:
        request: The concrete tool call that was executed.
        session_id: Session identifier owning this turn.
        call_index: Position of this tool call within the current tool-call batch.
        result: String result that will be persisted and/or fed back into history.
        tool_status: High-level execution status such as `ok` or `error`.
        force_finish: Whether the hook wants to stop the remaining tool batch and
            short-circuit the current turn after this tool completes.
        force_finish_reason: Optional structured reason describing why the hook
            requested the short-circuit.
    """

    request: LlmToolCallRequest
    session_id: str
    call_index: int
    result: str
    tool_status: str
    force_finish: bool = False
    force_finish_reason: str | None = None


class EventHookHandler(ABC, Generic[PayloadT]):
    """Base class for event hook scripts."""

    event_type: str

    @abstractmethod
    async def run(self, payload: PayloadT, ctx: HookExecutionContext) -> HookStageOutput[PayloadT]:
        """Execute one event hook."""
