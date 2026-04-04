# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio

from fairyclaw.core.gateway_protocol.models import BridgeFrame, GatewayOutboundMessage
from fairyclaw.gateway.adapters.http_adapter import HttpGatewayAdapter


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


def test_http_gateway_adapter_backlogs_outbound_without_subscriber() -> None:
    adapter = HttpGatewayAdapter()
    asyncio.run(
        adapter.send(
            GatewayOutboundMessage.text(
                session_id="sess_1",
                text="hello",
            )
        )
    )
    assert adapter._backlog["sess_1"][-1]["content"] == {"text": "hello"}
