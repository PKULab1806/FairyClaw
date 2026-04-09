# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio

from fairyclaw.core.gateway_protocol.models import BridgeFrame, GatewayOutboundMessage
from fairyclaw.gateway.adapters.web_gateway_adapter import WebGatewayAdapter


def test_bridge_frame_roundtrip_preserves_payload() -> None:
    raw = BridgeFrame(
        type="inbound",
        payload={
            "session_id": "sess_1",
            "adapter_key": "http",
            "segments": [{"type": "text", "text": "hello"}],
        },
        id="frm_1",
        ts_ms=123,
    ).to_json()
    frame = BridgeFrame.from_json(raw)
    assert frame.type == "inbound"
    assert frame.id == "frm_1"
    assert frame.payload["segments"][0]["text"] == "hello"


def test_gateway_outbound_message_roundtrip_preserves_route_hints() -> None:
    from dataclasses import replace

    base = GatewayOutboundMessage.text(session_id="sess_1", text="hi")
    msg = replace(
        base,
        adapter_key="onebot",
        sender_ref={"user_id": "123", "group_id": None},
    )
    restored = GatewayOutboundMessage.from_payload(msg.to_payload())
    assert restored.adapter_key == "onebot"
    assert restored.sender_ref == {"user_id": "123", "group_id": None}


def test_http_gateway_adapter_backlogs_outbound_without_subscriber() -> None:
    adapter = WebGatewayAdapter()
    asyncio.run(
        adapter.send(
            GatewayOutboundMessage.text(
                session_id="sess_1",
                text="hello",
            )
        )
    )
    assert adapter._backlog["sess_1"][-1]["op"] == "push"
    assert adapter._backlog["sess_1"][-1]["body"]["content"] == {"text": "hello"}
