# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""LLM client implementation.

This module wraps OpenAI-compatible chat endpoints and handles:
1. plain chat requests and tool-enabled chat requests;
2. retry logic and error logging;
3. normalization into the runtime `ChatResult` structure.
"""

import asyncio
import importlib
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Any

from fairyclaw.config.settings import settings
from fairyclaw.infrastructure.llm.config import LLMEndpointProfile

logger = logging.getLogger(__name__)

RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY_SECONDS = 2
TOOL_CHOICE_AUTO = "auto"
CONTENT_TYPE_JSON = "application/json"
REINS_AGENT_NAME = "fairyclaw_runtime"


@dataclass
class ToolCall:
    """Represent one tool call returned by model response."""

    id: str
    name: str
    arguments: str


@dataclass
class ChatResult:
    """Represent normalized model response payload.

    Attributes:
        text (str): Plain text response.
        tool_calls (list[ToolCall]): Parsed tool-call list.
    """

    text: str
    tool_calls: list[ToolCall]
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


class OpenAICompatibleLLMClient:
    """HTTP client wrapper for OpenAI-compatible chat-completions APIs."""

    def __init__(self, profile: LLMEndpointProfile, fallback_profile: LLMEndpointProfile | None = None):
        """Initialize client with primary and optional fallback profile.

        Args:
            profile (LLMEndpointProfile): Primary endpoint profile.
            fallback_profile (LLMEndpointProfile | None): Optional fallback profile.

        Returns:
            None
        """
        self.profile = profile
        self.fallback_profile = fallback_profile
        self._sdk_chat_call = self._call_chat_completion_sdk
        self._api_connection_error: type[Exception] = Exception
        self._api_timeout_error: type[Exception] = Exception
        self._api_status_error: type[Exception] = Exception
        self._async_openai_class: Any = None
        self._load_openai_symbols()
        trace_fn = self._load_reins_trace() if settings.reins_enabled else None
        if trace_fn is not None:
            self._sdk_chat_call = trace_fn(
                budget=settings.reins_budget_daily_usd,
                on_exceed=settings.reins_on_exceed,
                agent_name=REINS_AGENT_NAME,
            )(self._call_chat_completion_sdk)

    def is_available(self) -> bool:
        """Check whether API key for primary profile exists in environment.

        Returns:
            bool: True when configured API key environment variable is non-empty.
        """
        token = os.getenv(self.profile.api_key_env, "").strip()
        return bool(token)

    async def chat(self, messages: list[dict[str, Any]]) -> str:
        """Run plain chat request without tool schema.

        Args:
            messages (list[dict[str, Any]]): Chat message list.

        Returns:
            str: Assistant text response.
        """
        result = await self.chat_with_tools(messages=messages, tools=None)
        return result.text

    async def chat_with_tools(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> ChatResult:
        """Run chat request with optional tools and fallback handling.

        Args:
            messages (list[dict[str, Any]]): Chat message list.
            tools (list[dict[str, Any]] | None): OpenAI-style tool schemas.

        Returns:
            ChatResult: Normalized model response.

        Raises:
            Exception: Re-raises primary or fallback exception when both attempts fail.
        """
        try:
            return await self._chat_with_profile(self.profile, messages=messages, tools=tools)
        except Exception as primary_exc:
            if not self._should_try_fallback(primary_exc):
                raise
            fallback = self.fallback_profile
            if fallback is None:
                raise
            fallback_api_key = os.getenv(fallback.api_key_env, "").strip()
            if not fallback_api_key:
                logger.error(
                    f"Fallback profile '{fallback.name}' is configured but API key env '{fallback.api_key_env}' is missing."
                )
                raise
            logger.warning(
                f"Primary profile '{self.profile.name}' failed after retries. "
                f"Falling back to profile '{fallback.name}' model='{fallback.model}'."
            )
            try:
                return await self._chat_with_profile(fallback, messages=messages, tools=tools)
            except Exception as fallback_exc:
                logger.error(
                    f"Fallback profile '{fallback.name}' also failed: {type(fallback_exc).__name__} - {fallback_exc}"
                )
                raise fallback_exc from primary_exc

    async def _chat_with_profile(
        self,
        profile: LLMEndpointProfile,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> ChatResult:
        """Execute one chat request against a specific endpoint profile.

        Args:
            profile (LLMEndpointProfile): Target endpoint profile.
            messages (list[dict[str, Any]]): Chat message list.
            tools (list[dict[str, Any]] | None): Optional tool schemas.

        Returns:
            ChatResult: Parsed response payload.

        Raises:
            RuntimeError: Raised when API key is missing or payload contains explicit error.
            APIStatusError: Raised after retry budget is exhausted.
            APIConnectionError/APITimeoutError: Raised after retry budget is exhausted.
        """
        api_key = os.getenv(profile.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Missing API key env: {profile.api_key_env}")
        payload = {
            "model": profile.model,
            "messages": messages,
            "temperature": profile.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = TOOL_CHOICE_AUTO
        data: dict[str, Any] = {}
        for attempt in range(DEFAULT_MAX_RETRIES):
            try:
                data = await self._sdk_chat_call(
                    profile=profile,
                    api_key=api_key,
                    payload=payload,
                    metadata={
                        "profile": profile.name,
                        "model": profile.model,
                    },
                )
                self._raise_if_payload_error(data)
                break
            except self._api_status_error as e:
                status_code = getattr(e, "status_code", 0) or 0
                error_body = self._extract_status_error_body(e)
                if status_code in RETRYABLE_HTTP_STATUS_CODES and attempt < DEFAULT_MAX_RETRIES - 1:
                    delay = DEFAULT_BASE_DELAY_SECONDS * (2**attempt)
                    logger.warning(
                        f"HTTP {status_code} error from LLM endpoint profile='{profile.name}' model='{profile.model}'. "
                        f"Body: {error_body}. Retrying in {delay}s... (Attempt {attempt + 1}/{DEFAULT_MAX_RETRIES})"
                    )
                    await asyncio.sleep(delay)
                    continue
                if status_code == 400:
                    self._log_bad_request_details(
                        profile=profile,
                        payload=payload,
                        error_body=error_body,
                    )
                logger.error(
                    f"HTTP error {status_code} occurred on profile='{profile.name}' model='{profile.model}': {e}. "
                    f"Body: {error_body}"
                )
                raise
            except (self._api_connection_error, self._api_timeout_error) as e:
                if attempt < DEFAULT_MAX_RETRIES - 1:
                    delay = DEFAULT_BASE_DELAY_SECONDS * (2**attempt)
                    logger.warning(
                        f"Network/Request error from LLM endpoint profile='{profile.name}' model='{profile.model}' "
                        f"(No HTTP status code): {type(e).__name__} - {e}. Retrying in {delay}s... "
                        f"(Attempt {attempt + 1}/{DEFAULT_MAX_RETRIES})"
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    f"Network/Request error on profile='{profile.name}' model='{profile.model}': "
                    f"{type(e).__name__} - {e}"
                )
                raise
        return self._parse_chat_result(data)

    def _should_try_fallback(self, exc: Exception) -> bool:
        """Check whether an exception is eligible for fallback profile retry.

        Args:
            exc (Exception): Exception raised by primary request.

        Returns:
            bool: True when fallback should be attempted.
        """
        if isinstance(exc, (self._api_connection_error, self._api_timeout_error)):
            return True
        if isinstance(exc, self._api_status_error):
            return ((getattr(exc, "status_code", 0) or 0) in RETRYABLE_HTTP_STATUS_CODES)
        return False

    async def _call_chat_completion_sdk(
        self,
        *,
        profile: LLMEndpointProfile,
        api_key: str,
        payload: dict[str, Any],
        metadata: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Call OpenAI-compatible endpoint using official SDK."""
        if self._async_openai_class is None:
            raise RuntimeError("openai SDK is not available; please install project dependencies.")
        client = self._async_openai_class(
            base_url=profile.api_base.rstrip("/"),
            api_key=api_key,
            timeout=profile.timeout_seconds,
        )
        request_args: dict[str, Any] = dict(payload)
        # Reins/OpenAI instrumentation can use metadata for downstream tracing.
        if metadata:
            request_args["metadata"] = metadata
        completion = await client.chat.completions.create(**request_args)
        return completion.model_dump()

    def _extract_status_error_body(self, exc: Exception) -> str:
        """Extract readable body text from OpenAI SDK status error."""
        response = getattr(exc, "response", None)
        if response is None:
            return str(exc)
        text = getattr(response, "text", None)
        if isinstance(text, str) and text:
            return text
        body = getattr(response, "body", None)
        if body is not None:
            try:
                return json.dumps(body, ensure_ascii=False)
            except Exception:
                return str(body)
        return str(exc)

    def _load_openai_symbols(self) -> None:
        """Load OpenAI SDK symbols lazily to avoid hard import-time failures."""
        try:
            openai_mod = importlib.import_module("openai")
        except Exception:
            return
        self._async_openai_class = getattr(openai_mod, "AsyncOpenAI", None)
        self._api_connection_error = getattr(openai_mod, "APIConnectionError", Exception)
        self._api_timeout_error = getattr(openai_mod, "APITimeoutError", Exception)
        self._api_status_error = getattr(openai_mod, "APIStatusError", Exception)

    def _load_reins_trace(self) -> Any | None:
        """Load Reins trace decorator lazily; return None when unavailable."""
        try:
            reins_mod = importlib.import_module("reins")
        except Exception:
            return None
        return getattr(reins_mod, "trace", None)

    @staticmethod
    def _parse_dsml_tool_calls(reasoning: str) -> list[ToolCall]:
        """Parse DeepSeek DSML-format tool calls from reasoning_content.

        DeepSeek-v3 series models occasionally emit tool calls inside
        ``reasoning_content`` using DSML XML tags instead of the standard
        ``tool_calls`` JSON field.  When this happens the standard field is
        empty and nothing gets executed.  This method extracts those calls so
        they are treated identically to regular tool calls.

        DSML example::

            <｜DSML｜function_calls>
            <｜DSML｜invoke name="run_command">
            <｜DSML｜parameter name="command" string="true">echo hi</｜DSML｜parameter>
            </｜DSML｜invoke>
            </｜DSML｜function_calls>
        """
        if not reasoning or "<｜DSML｜" not in reasoning:
            return []
        calls: list[ToolCall] = []
        invoke_pattern = re.compile(
            r"<｜DSML｜invoke\s+name=['\"]([^'\"]+)['\"]>(.*?)</｜DSML｜invoke>",
            re.DOTALL,
        )
        param_pattern = re.compile(
            r"<｜DSML｜parameter\s+name=['\"]([^'\"]+)['\"][^>]*>(.*?)</｜DSML｜parameter>",
            re.DOTALL,
        )
        for invoke_match in invoke_pattern.finditer(reasoning):
            tool_name = invoke_match.group(1).strip()
            invoke_body = invoke_match.group(2)
            arguments: dict[str, str] = {}
            for param_match in param_pattern.finditer(invoke_body):
                arguments[param_match.group(1).strip()] = param_match.group(2).strip()
            calls.append(
                ToolCall(
                    id=f"dsml_{uuid.uuid4().hex[:8]}",
                    name=tool_name,
                    arguments=json.dumps(arguments, ensure_ascii=False),
                )
            )
        return calls

    def _parse_chat_result(self, data: dict[str, Any]) -> ChatResult:
        """Parse raw API payload into ChatResult structure.

        Args:
            data (dict[str, Any]): API response payload.

        Returns:
            ChatResult: Normalized text and tool-call list.
        """
        choices = data.get("choices", []) if isinstance(data, dict) else []
        if not choices:
            return ChatResult(text="", tool_calls=[])
        message = choices[0].get("message", {})
        text = self._normalize_message_content(message.get("content"))
        calls: list[ToolCall] = []
        for call in message.get("tool_calls", []) or []:
            function = call.get("function", {})
            calls.append(
                ToolCall(
                    id=str(call.get("id", "")),
                    name=str(function.get("name", "")),
                    arguments=str(function.get("arguments", "{}")),
                )
            )
        # Fall back to DSML tool calls embedded in reasoning_content when the
        # standard tool_calls field is empty (DeepSeek-v3 series behaviour).
        if not calls:
            reasoning = message.get("reasoning_content") or ""
            dsml_calls = self._parse_dsml_tool_calls(reasoning)
            if dsml_calls:
                logger.debug(
                    "Extracted %d tool call(s) from reasoning_content DSML (standard tool_calls was empty)",
                    len(dsml_calls),
                )
                calls = dsml_calls
        usage = data.get("usage", {}) if isinstance(data, dict) else {}
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        completion_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else None
        return ChatResult(
            text=text,
            tool_calls=calls,
            prompt_tokens=int(prompt_tokens) if isinstance(prompt_tokens, int) else None,
            completion_tokens=int(completion_tokens) if isinstance(completion_tokens, int) else None,
            total_tokens=int(total_tokens) if isinstance(total_tokens, int) else None,
        )

    def _normalize_message_content(self, content: Any) -> str:
        """Normalize model `content` payloads into plain text."""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return "\n".join(parts).strip()
        return str(content or "").strip()

    def _raise_if_payload_error(self, data: dict[str, Any]) -> None:
        """Raise an exception when the payload contains an explicit error object."""
        if "error" not in data:
            return
        err_obj = data["error"]
        err_code = err_obj.get("code", "unknown_code") if isinstance(err_obj, dict) else "unknown_code"
        err_msg = err_obj.get("message", str(err_obj)) if isinstance(err_obj, dict) else str(err_obj)
        logger.error(f"LLM API returned error in payload (Status 200): [{err_code}] {err_msg}")
        raise RuntimeError(f"LLM API Payload Error: [{err_code}] {err_msg}")

    def _log_bad_request_details(
        self,
        profile: LLMEndpointProfile,
        payload: dict[str, Any],
        error_body: str,
    ) -> None:
        """Log rich context for HTTP 400 responses to debug schema/argument issues."""
        url = f"{profile.api_base.rstrip('/')}/chat/completions"
        safe_headers = {
            "Authorization": "Bearer ***",
            "Content-Type": CONTENT_TYPE_JSON,
        }
        error_details = (
            "=== HTTP 400 BAD REQUEST DETAILED LOG ===\n"
            f"URL: {url}\n"
            f"Profile: {profile.name}\n"
            f"Model: {profile.model}\n"
            f"Request Headers: {json.dumps(safe_headers, indent=2)}\n"
            "Response Status: 400\n"
            f"Response Body: {error_body}\n"
            f"Request Payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
            "==========================================="
        )
        logger.error(error_details)
