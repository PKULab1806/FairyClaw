# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors, PKU DS Lab
import asyncio

from fairyclaw.core.events.runtime import deliver_file_to_user, set_file_delivery


def test_runtime_file_delivery_calls_registered_handler() -> None:
    async def scenario() -> None:
        delivered: list[tuple[str, str]] = []

        async def handler(session_id: str, file_id: str) -> None:
            delivered.append((session_id, file_id))

        set_file_delivery(handler)
        try:
            await deliver_file_to_user("sess_1", "file_1")
        finally:
            set_file_delivery(None)

        assert delivered == [("sess_1", "file_1")]

    asyncio.run(scenario())
