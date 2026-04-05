# Contributing to FairyClaw

Contributions of all kinds are welcome — bug reports, feature requests, documentation improvements, and new capability groups.

---

## Getting Started

1. Fork the repository and create a branch for your work.
2. Install in development mode:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e .
   ```
3. Run the test suite to confirm a clean baseline:
   ```bash
   python -m pytest tests/ -q
   ```
4. Open a pull request against `main` with a clear description of the change.

For bugs or questions, open a GitHub Issue first — especially for changes that touch the core scheduler, event bus, or Hook semantics.

---

## The Main Contribution Area: Capability Groups

The fastest way to extend FairyClaw without touching the core is to add a **capability group** under `fairyclaw/capabilities/`. Each group is a self-contained directory with a `manifest.json` and one or more Python scripts.

```
fairyclaw/capabilities/
├── agent_tools/          ← built-in example
│   ├── manifest.json
│   └── scripts/
│       ├── delegate_task.py
│       └── ...
├── your_new_group/       ← add yours here
│   ├── manifest.json
│   ├── config.py         (optional — define runtime_config_model here)
│   ├── config.yaml       (optional — large/structured static config)
│   └── scripts/
│       └── your_tool.py
```

### `manifest.json` structure

A minimal manifest with one Tool and one Hook:

```json
{
  "name": "MyGroup",
  "description": "What this group does.",
  "always_enable_planner": false,
  "always_enable_subagent": false,
  "capabilities": [
    {
      "name": "my_tool",
      "description": "Does something useful.",
      "type": "Tool",
      "schema": {
        "parameters": {
          "type": "object",
          "properties": {
            "input": { "type": "string", "description": "The input value." }
          },
          "required": ["input"]
        }
      },
      "script": "my_tool.py"
    }
  ],
  "hooks": [
    {
      "name": "my_before_llm_hook",
      "stage": "before_llm_call",
      "script": "my_hook.py",
      "priority": 50,
      "enabled": true,
      "timeout_ms": 300,
      "on_error": "warn",
      "config": {}
    }
  ]
}
```

See [`fairyclaw/capabilities/agent_tools/manifest.json`](fairyclaw/capabilities/agent_tools/manifest.json) for a full Tool example and [`fairyclaw/capabilities/runtime_event_hooks/manifest.json`](fairyclaw/capabilities/runtime_event_hooks/manifest.json) for how to declare custom `event_types` hooks.

### Imports: always use `fairyclaw.sdk`

Capability scripts must import from `fairyclaw.sdk.*` rather than directly from `fairyclaw.core.*`.  The SDK is the stable, versioned public surface for group code.

```python
from fairyclaw.sdk.tools import ToolContext, resolve_safe_path
from fairyclaw.sdk.hooks import HookStageInput, HookStageOutput, HookStatus
from fairyclaw.sdk.subtasks import get_or_create_subtask_state
from fairyclaw.sdk.runtime import publish_user_message_received, request_planner_wakeup
from fairyclaw.sdk.events import EventType
```

Direct imports like `from fairyclaw.core.capabilities.models import ToolContext` are discouraged in group scripts and will be flagged in code review.

### Group runtime configuration

If your group needs runtime parameters (timeouts, proxy URLs, model names, etc.), define a frozen Pydantic model in `config.py` and expose it as `runtime_config_model`.  The registry loads it once at startup and injects it into `ToolContext.group_runtime_config`.

```python
# fairyclaw/capabilities/my_group/config.py
from pydantic import BaseModel

class MyGroupRuntimeConfig(BaseModel):
    model_config = {"frozen": True}
    my_timeout_seconds: int = 30
    my_api_key: str | None = None

runtime_config_model = MyGroupRuntimeConfig
```

In scripts, retrieve the snapshot with `expect_group_config`:

```python
from fairyclaw.sdk.group_runtime import expect_group_config
from fairyclaw.capabilities.my_group.config import MyGroupRuntimeConfig

async def execute(args, context: ToolContext) -> str:
    cfg = expect_group_config(context, MyGroupRuntimeConfig)
    timeout = cfg.my_timeout_seconds
```

Configure group-specific values in `config/fairyclaw.env` using the `FAIRYCLAW_CAP_<GROUP>__<FIELD>` prefix (double underscore):

```
FAIRYCLAW_CAP_MY_GROUP__MY_TIMEOUT_SECONDS=60
FAIRYCLAW_CAP_MY_GROUP__MY_API_KEY=sk-...
```

**Do not** import or read `fairyclaw.config.settings` from capability scripts for group-specific parameters.  Process-level values (e.g. `filesystem_root_dir`) are injected into `ToolContext` directly and accessible as `context.filesystem_root_dir`.

### Tool scripts

Tool scripts export an `async def execute(args: dict, context: ToolContext) -> str` function:

```python
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors

from typing import Any, Dict
from fairyclaw.sdk.tools import ToolContext

async def execute(args: Dict[str, Any], context: ToolContext) -> str:
    result = do_something(args["input"])
    return result
```

### Hook scripts

Hooks intercept the five agent lifecycle stages. Import hook types from `fairyclaw.sdk.hooks`.

**Five stages and their payload types:**

| Stage constant | `HookStage` value | Payload type |
|---|---|---|
| `TOOLS_PREPARED` | `"tools_prepared"` | `ToolsPreparedHookPayload` |
| `BEFORE_LLM_CALL` | `"before_llm_call"` | `BeforeLlmCallHookPayload` |
| `AFTER_LLM_RESPONSE` | `"after_llm_response"` | `AfterLlmResponseHookPayload` |
| `BEFORE_TOOL_CALL` | `"before_tool_call"` | `BeforeToolCallHookPayload` |
| `AFTER_TOOL_CALL` | `"after_tool_call"` | `AfterToolCallHookPayload` |

**Key types for hook authors:**

- `HookExecutionContext` — session metadata: `session_id`, `turn_id`, `task_type`, `is_sub_session`, `enabled_groups`, `token_budget`.
- `HookStageInput[PayloadT]` — wraps `(stage, context, payload, budget, metadata)`.
- `HookStageOutput[PayloadT]` — return `(status, patched_payload, artifacts, metrics, error)`.

`BeforeLlmCallHookPayload` is the most commonly used: it provides access to the full typed history IR (`history_items: list[ChatHistoryItem]`), the current user turn (`user_turn: UserTurn`), and the assembled LLM messages (`llm_messages`).

A minimal hook entry point:

```python
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors

from fairyclaw.sdk.hooks import (
    HookStageInput,
    HookStageOutput,
    HookStatus,
    BeforeLlmCallHookPayload,
)


async def execute(hook_input: HookStageInput[BeforeLlmCallHookPayload]) -> HookStageOutput[BeforeLlmCallHookPayload]:
    payload = hook_input.payload
    # Inspect or mutate payload here, then return it
    return HookStageOutput(status=HookStatus.OK, patched_payload=payload)
```

### Runtime event hooks

To react to system events (e.g. `file_upload_received`) declare a hook with `"stage": "event:<event_name>"`. Custom events can be defined by adding an `"event_types"` list to the manifest. See [`fairyclaw/capabilities/runtime_event_hooks/manifest.json`](fairyclaw/capabilities/runtime_event_hooks/manifest.json) for an example.

---

## Core Changes

If your change touches the Planner, event bus, scheduler, or Hook semantics, please:

1. Open an Issue to discuss the design before implementing.
2. Update [`AI_SYSTEM_GUIDE.md`](AI_SYSTEM_GUIDE.md) to reflect any behavioral or architectural changes.
3. Add tests under `tests/` covering the new behavior.

---

## Code Style

- Python 3.10+, type annotations throughout.
- Follow the import and naming conventions of the surrounding code.
- No drive-by refactors — keep diffs focused.
- Add SPDX header to new files (see existing files for the format).
