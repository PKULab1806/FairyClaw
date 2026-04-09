# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
"""Tests for control envelope types."""

from fairyclaw.core.gateway_protocol.control_envelope import (
    EVENT_TYPE_TOOL_CALL,
    EVENT_TYPE_TOOL_RESULT,
    HeartbeatInfo,
    SYSTEM_ENV_WHITELIST,
    TelemetrySnapshot,
    ToolCallEnvelope,
    ToolResultEnvelope,
    parse_tool_arguments_json,
    validate_system_env_slice,
)
from fairyclaw.core.gateway_protocol.models import OUTBOUND_KIND_EVENT, GatewayOutboundMessage


def test_telemetry_snapshot_roundtrip() -> None:
    snap = TelemetrySnapshot(
        heartbeat=HeartbeatInfo(status="HEARTBEAT_OK", server_time_ms=123, message=None),
        reins_enabled=True,
    )
    d = snap.to_dict()
    assert d["heartbeat"]["status"] == "HEARTBEAT_OK"
    assert d["reins_enabled"] is True


def test_validate_system_env_slice_filters_keys() -> None:
    out = validate_system_env_slice(
        {
            "FAIRYCLAW_HOST": "0.0.0.0",
            "FAIRYCLAW_CAP_CORE_OPS__EXECUTION_TIMEOUT_SECONDS": "60",
        }
    )
    assert out == {"FAIRYCLAW_HOST": "0.0.0.0"}
    assert "FAIRYCLAW_CAP_CORE_OPS__EXECUTION_TIMEOUT_SECONDS" not in out


def test_system_whitelist_contains_reins() -> None:
    assert "FAIRYCLAW_REINS_BUDGET_DAILY_USD" in SYSTEM_ENV_WHITELIST


def test_gateway_outbound_event_factory() -> None:
    msg = GatewayOutboundMessage.event(
        "sess_1",
        event_type="telemetry",
        content={"reins_enabled": True},
        meta={"trace": "t"},
    )
    assert msg.kind == OUTBOUND_KIND_EVENT
    assert msg.content["event_type"] == "telemetry"


def test_tool_envelopes_roundtrip() -> None:
    pre = ToolCallEnvelope(tool_call_id="tc_1", tool_name="search", arguments={"q": "x"})
    d_pre = pre.to_content_dict()
    assert d_pre["tool_name"] == "search"
    assert d_pre["arguments"]["q"] == "x"
    msg_pre = GatewayOutboundMessage.event("s", event_type=EVENT_TYPE_TOOL_CALL, content=d_pre)
    assert msg_pre.content["event_type"] == EVENT_TYPE_TOOL_CALL

    post = ToolResultEnvelope(
        tool_call_id="tc_1",
        tool_name="search",
        ok=False,
        error_message="boom",
    )
    d_post = post.to_content_dict()
    assert d_post["ok"] is False
    assert "result" not in d_post
    msg_post = GatewayOutboundMessage.event("s", event_type=EVENT_TYPE_TOOL_RESULT, content=d_post)
    assert msg_post.content["event_type"] == EVENT_TYPE_TOOL_RESULT


def test_parse_tool_arguments_json() -> None:
    assert parse_tool_arguments_json('{"a":1}') == {"a": 1}
    assert parse_tool_arguments_json("not json") == {"_raw": "not json"}
