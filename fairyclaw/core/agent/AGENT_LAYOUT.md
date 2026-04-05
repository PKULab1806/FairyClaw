# Agent Package Layout

`fairyclaw/core/agent` 负责单步推理编排、上下文构建、会话状态管理与能力路由。为降低耦合，目录按职责拆分为可组合子层。

| Path | Responsibility | Notes |
| --- | --- | --- |
| `planning/` | 会话单步编排、主/子策略分流与子任务协调入口 | `Planner` 保留共享 orchestration，`turn_policy.py` 承担主/子差异 |
| `context/` | 历史 IR、系统提示与消息装配 | 已不再承担 system-facing 的裸 `dict -> IR` 主路径 |
| `session/` | 会话角色策略、会话锁与内存读写 | 与数据库/持久化耦合，避免反向依赖规划层 |
| `routing/` | 能力组选择与路由策略 | `ToolRouter` 仅依赖 capability profiles |
| `hooks/` | Hook 协议、执行器与阶段调度器 | Turn hook 全程使用 typed payload，同 stage 内输入输出同型 |
| `executors/` | 上下文/工具执行流水线 | 组合 `hooks` 和 `planning`，不持有全局状态 |
| `interfaces/` | 协议与抽象接口 | 当前仅保留 `MemoryProvider`，其余未接线 ABC 已移除 |
| root (`constants.py`, `types.py`) | 跨层通用常量与轻量类型 | 根目录不再保留 shim/re-export；实现以子包为唯一来源 |

## SDK Dependency Direction

`fairyclaw.sdk` sits **between** capability scripts and `fairyclaw.core`:

- `fairyclaw/capabilities/<group>/scripts/*.py` → `fairyclaw.sdk.*`
- `fairyclaw.sdk.*` → `fairyclaw.core.*`
- `fairyclaw.sdk` **never** imports from `fairyclaw.capabilities`

`CapabilityRegistry` loads group config models from `<group>/config.py` (looking for `runtime_config_model`) and calls `fairyclaw.sdk.group_runtime.load_group_runtime_config` at startup.  The resulting frozen snapshot is stored on `CapabilityGroup.runtime_config` and injected into `ToolContext.group_runtime_config` by `fairyclaw.tools.runtime.ToolRuntime` when executing tools.

## Dependency Direction

- `planning -> context/hooks/executors/session/routing`
- `executors -> hooks/interfaces`
- `context -> types/domain`
- `session -> infrastructure + domain`
- `routing -> capabilities + llm factory`
- `hooks -> capabilities registry + hook runtime`

补充边界：

- `session/memory.py -> context/history_ir.py`
- `session/memory.py` 向上游直接暴露 `ChatHistoryItem` IR，不再暴露中间 `dict`
- `context/llm_message_assembler.py` 只负责 `IR -> LlmChatMessage`

禁止方向：

- `hooks` 依赖 `SessionEventBus`（跨 turn 分发与 turn 内 Hook 流水线语义不同）
- `context` 直接依赖 `main.py` 或 FastAPI 层
- `routing` 依赖 session runtime state
- `context` 重新承担 system-facing 的裸历史解析

## Context Layer

`context/` 当前应按三层理解：

- `history_ir.py`
  - 定义系统内部 IR：`SessionMessageBlock`、`ToolCallRound`、`UserTurn`
  - 这是 hook 应优先消费的历史/轮次语义对象
- `llm_message_assembler.py`
  - 把 IR 组装成 `LlmChatMessage`
  - `LlmChatMessage` 是 LLM 边界对象，不是业务 IR
- `turn_context_builder.py`
  - 负责把“过往历史”与“当前轮用户输入”拆开
  - 会把当前轮 user message 显式提取为 `user_turn`
  - 同时避免同一条用户消息既留在 `history_items` 又出现在 `user_turn`

当前已移除：

- `history_mapper.py`
  - 原本承担 `dict -> IR` 转换
  - 该职责已下沉到 `session/memory.py`

## Hook Contract

`hooks/protocol.py` 中最关键的 turn 语义如下：

- `LlmTurnContext.history_items`
  - 完整 typed 历史 IR
  - 是 hook 读取历史语义的权威来源
- `LlmTurnContext.user_turn`
  - 当前 planner cycle 的用户输入 IR
  - 不应再被历史末尾消息隐式替代
- `LlmTurnContext.llm_messages`
  - 由 IR 派生出的 provider-facing 请求消息
  - hook 可改写它来影响最终发给模型的请求

五个 turn hook stage：

- `tools_prepared`
- `before_llm_call`
- `after_llm_response`
- `before_tool_call`
- `after_tool_call`

约束：

- `HookStageInput.payload` / `HookStageOutput.patched_payload` 必须是明确类型对象
- 同一 stage 内，前一个 hook 的输出对象直接作为下一个 hook 的输入对象
- `HookRuntime` 不再支持 `dict.update(...)` 式 payload merge

## Planning Layer

`planning/` 当前不再通过 `_is_sub_session(...)` 在多处硬分支主/子逻辑，而是：

- `Planner`
  - 负责共享 orchestration：取历史、构建 turn、调用 hooks、调用 LLM、执行工具
- `turn_policy.py`
  - 定义 `MainSessionTurnPolicy` / `SubSessionTurnPolicy`
  - 负责主/子会话差异：skip、text response、follow-up、failure handling
- `subtask_coordinator.py`
  - 负责 subtask 终态、即时失败通知、屏障聚合

## Session / Memory Boundary

`MemoryProvider` 当前只保留 planner 真正需要的职责：

- `get_history(session_id) -> list[ChatHistoryItem]`
- `add_session_event(session_id, message: SessionMessageBlock)`
- `add_operation_event(session_id, tool_round: ToolCallRound)`

说明：

- `query_memory` 已删除
- `PersistentMemory` 负责把数据库行解析为 IR
- planner / context 不再接触历史 `dict`

## Runtime Boundary

- 事件消费和会话调度由 `fairyclaw/core/events/` 承接。
- Planner 仅处理单次 turn 编排。
- Hook 相关逻辑由 `fairyclaw/core/agent/hooks/` 处理，其中 `HookStageRunner` 负责 stage-level 执行协调。
- runtime event plugin dispatch 与 turn hook pipeline 是两套边界：
  - 前者处理 `event:*`
  - 后者处理单次 turn 五阶段 hook
