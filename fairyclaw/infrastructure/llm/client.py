# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""LLM client implementation.

This module wraps OpenAI-compatible chat endpoints and handles:
1. plain chat requests and tool-enabled chat requests;
2. retry logic and error logging;
3. normalization into the runtime `ChatResult` structure.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import httpx

from fairyclaw.infrastructure.llm.config import LLMEndpointProfile

logger = logging.getLogger(__name__)

RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY_SECONDS = 2
TOOL_CHOICE_AUTO = "auto"
CONTENT_TYPE_JSON = "application/json"


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
            httpx.HTTPStatusError: Raised after retry budget is exhausted.
            httpx.RequestError: Raised after retry budget is exhausted.
        """
        api_key = os.getenv(profile.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Missing API key env: {profile.api_key_env}")
        url = f"{profile.api_base.rstrip('/')}/chat/completions"
        payload = {
            "model": profile.model,
            "messages": messages,
            "temperature": profile.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = TOOL_CHOICE_AUTO
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": CONTENT_TYPE_JSON,
        }
        data: dict[str, Any] = {}
        for attempt in range(DEFAULT_MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=profile.timeout_seconds) as client:
                    response = await client.post(url, json=payload, headers=headers)
                    response.raise_for_status()
                    data = response.json()
                    self._raise_if_payload_error(data)
                    break
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                error_body = e.response.text
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
                        url=url,
                        headers=headers,
                        payload=payload,
                        response=e.response,
                    )
                logger.error(
                    f"HTTP error {status_code} occurred on profile='{profile.name}' model='{profile.model}': {e}. "
                    f"Body: {error_body}"
                )
                raise
            except httpx.RequestError as e:
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
        if isinstance(exc, httpx.RequestError):
            return True
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in RETRYABLE_HTTP_STATUS_CODES
        return False

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
        return ChatResult(text=text, tool_calls=calls)

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
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        response: httpx.Response,
    ) -> None:
        """Log rich context for HTTP 400 responses to debug schema/argument issues."""
        safe_headers = dict(headers)
        if "Authorization" in safe_headers:
            safe_headers["Authorization"] = "Bearer ***"
        error_details = (
            "=== HTTP 400 BAD REQUEST DETAILED LOG ===\n"
            f"URL: {url}\n"
            f"Profile: {profile.name}\n"
            f"Model: {profile.model}\n"
            f"Request Headers: {json.dumps(safe_headers, indent=2)}\n"
            f"Response Status: {response.status_code}\n"
            f"Response Headers: {json.dumps(dict(response.headers), indent=2)}\n"
            f"Response Body: {response.text}\n"
            f"Request Payload:\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
            "==========================================="
        )
        logger.error(error_details)
