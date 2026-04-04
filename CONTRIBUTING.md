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
│   ├── config.yaml       (optional)
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

### Tool scripts

A Tool script receives a JSON context object from stdin and writes a result to stdout:

```python
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 FairyClaw contributors

import json, sys

context = json.load(sys.stdin)
params = context["parameters"]

result = do_something(params["input"])
print(json.dumps({"result": result}))
```

### Hook scripts

Hooks intercept the five agent lifecycle stages. The canonical hook boundary types live in [`fairyclaw/core/agent/hooks/protocol.py`](fairyclaw/core/agent/hooks/protocol.py).

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

from fairyclaw.core.agent.hooks.protocol import (
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
