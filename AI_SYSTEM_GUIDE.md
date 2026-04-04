# FairyClaw AI 开发与架构指南 (AI System Guide)

这是一份专为 AI 助手（及人类开发者）准备的 FairyClaw 运行时与开发指南。本文已同步到当前实现，重点覆盖近期架构重构：事件总线唤醒模型、typed history/IR、五阶段 Hook、单步 Planner、Sub-Agent 屏障聚合、能力组拆分与单次委派路由。

## 1. 当前系统定位

FairyClaw 是一个基于异步事件驱动的 Agent 框架，核心目标是把“用户请求处理”从同步阻塞改为可持续推进的后台任务模型。

关键特征：
- 基于 Runtime Event Bus 的会话级调度，不再依赖传统单请求内长循环。
- Planner 每次唤醒只做单步推理，然后通过事件继续推进后续步骤。
- 支持主 Agent 委派多个 Sub-Agent 并发执行，最终通过屏障聚合结果回流主会话。
- Tool/Skill 通过 Manifest + Script 动态注册，保持插件化扩展。
- 能力组可在 `manifest.json` 中声明自定义 `event_types`，供插件化 runtime event 分发使用。
- Hook 生命周期固定为五阶段：`tools_prepared` / `before_llm_call` / `after_llm_response` / `before_tool_call` / `after_tool_call`。
- `before_llm_call` 默认拿到完整 typed history IR，而不是历史 `dict`。
- 支持 Hook 通过 `force_finish` 在选定 stage 上静默短路当前 turn：发布事件但不继续当轮/下一轮推理。

## 2. 目录与关键模块

- [`LAYOUT.md`](LAYOUT.md)
  - 模块职责地图：顶层目录、核心事件系统、agent 子目录、能力组、基础设施各层职责一览。
- `fairyclaw/main.py`
  - 启动 `SessionEventBus` 与运行时调度器，负责依赖组装和生命周期托管。
- `fairyclaw/core/events/bus.py`
  - 定义 `RuntimeEvent`、`RuntimeEventEnvelope`、`SessionEventBus`。
  - 事件类型允许 `EventType | str`；核心事件仍保留 enum，自定义事件使用 manifest 声明后的字符串名称。
  - 内部订阅表统一按规范化后的字符串 key 存储，但用 `EventTypeKey` 包装，避免 `EventType.X` 与 `"x"` 双 key 并存。
- `fairyclaw/core/events/session_scheduler.py`（新）
  - 承担核心会话调度状态机：`mailbox` 消费、`debounce`、`run_session`、`heartbeat_watchdog`。
  - 通过单入口 `on_event` 统一接收所有 runtime event。
  - 核心三事件：`USER_MESSAGE_RECEIVED` / `SUBTASK_COMPLETED` / `WAKEUP_REQUESTED` 走 mailbox + wakeup 状态机。
  - 其它事件统一交给 `EventPluginDispatcher` 做 `event:*` hook 分发；manifest 声明的自定义事件只走这一条支路，不进入 Planner。
- `fairyclaw/core/agent/planning/planner.py`
  - 单步 Planner 核心：读取 typed `TurnRequest`，执行共享 orchestration。
  - 主/子会话差异已抽到 `turn_policy.py`，不再靠多处 `_is_sub_session(...)` 分支维持。
  - 主会话不参与工具组路由，仅按 `always_enable_planner=true` 自动启用能力组。
  - Sub-Agent 终态与屏障聚合委托 `SubtaskCoordinator`。
- `fairyclaw/core/agent/planning/turn_policy.py`（新）
  - 定义 `MainSessionTurnPolicy` / `SubSessionTurnPolicy`，承接 skip、follow-up、failure handling 等主/子差异。
- `fairyclaw/core/agent/planning/subtask_coordinator.py`
  - 统一处理子任务终态标记、即时失败通知、批次屏障聚合发布。
- `fairyclaw/core/agent/context/turn_context_builder.py`
  - 从 typed history 构造 `LlmTurnContext` 所需内容。
  - 负责把“过往历史”与“当前轮用户输入”拆开，显式生成 `user_turn`。
- `fairyclaw/core/agent/context/history_ir.py`
  - 定义 `SessionMessageBlock` / `MessageBody` / `ToolCallRound` / `UserTurn`。
- `fairyclaw/core/agent/context/llm_message_assembler.py`
  - 把 IR 组装成 `LlmChatMessage`。
  - 会把连续 `ToolCallRound` 聚合回一条 assistant `tool_calls` 消息，避免历史回放把一批 tool calls 拆成多条空 assistant 消息。
- `fairyclaw/core/agent/planning/planner_core.py`
  - 提供共享编排核心依赖装配（registry/router/hook runtime/LLM client 解析）。
- `fairyclaw/core/agent/session/session_role.py`
  - 提供主会话与子会话行为策略（是否允许回调用户、文本回合是否自动终态）。
- `fairyclaw/core/agent/interfaces/memory_provider.py`
  - 当前仅保留一个活接口：`MemoryProvider`
  - `get_history()` 直接返回 `ChatHistoryItem` IR
  - 新增可选压缩快照接口：`get_latest_compaction()` / `create_compaction_snapshot()`
- `fairyclaw/core/agent/session/memory.py`
  - `PersistentMemory` 负责“数据库行 -> IR”解析，并写入 typed `SessionMessageBlock` / `ToolCallRound`
  - 同时负责读写 `memory_compactions` 表中的摘要快照
- `fairyclaw/core/agent/session/global_state.py`
  - Sub-Agent 记录、终态判断、聚合结果构造。
  - 子任务批次号管理（新）：保留全历史记录，同时仅按当前批次聚合。
- `fairyclaw/core/capabilities/registry.py`
  - 动态加载能力组；支持脚本路径 `resolve()`（新，支持跨组复用脚本）。
  - 新增 Hook 注册、启用组解析、manifest `event_types` 解析，以及按阶段取 Hook。
- `fairyclaw/core/agent/hooks/protocol.py`
  - 定义 Hook 输入输出协议（`HookExecutionContext`、`HookStageInput/Output`）。
  - 使用五时机强类型 payload：`ToolsPreparedHookPayload`、`BeforeLlmCallHookPayload`、`AfterLlmResponseHookPayload`、`BeforeToolCallHookPayload`、`AfterToolCallHookPayload`。
  - `LlmTurnContext` 同时承载：
    - `history_items`: 完整 typed 历史 IR
    - `user_turn`: 当前轮用户输入 IR
    - `llm_messages`: 由 IR 派生出的 provider-facing 消息
- `fairyclaw/api/outbound/session_text_outbound.py`
  - （已移除）旧的 callback_url 出站投递实现已删除。当前出站由 Gateway 进程通过 WS Bridge 承接。
- `fairyclaw/core/agent/hooks/runtime.py`
  - 统一 Hook 执行、超时、错误策略（continue/fail/warn）。
  - 同一 stage 内只允许 typed payload 链式流动，不再支持 `dict` merge。
- `fairyclaw/core/agent/hooks/hook_stage_runner.py`（新）
  - 按 stage 解析已启用 hooks 并委托 `HookRuntime` 执行，不占用 `SessionEventBus`。
- `fairyclaw/capabilities/agent_tools/manifest.json`
  - 主 Planner 相关工具：`delegate_task`、`get_subtask_status`、`kill_subtask`、`message_subtask`。
- `fairyclaw/capabilities/sub_agent_tools/manifest.json`（新）
  - 子 Agent 专属工具：`report_subtask_done`。

## 3. 事件驱动执行模型（当前真实链路）

### 3.1 从用户消息到 Planner 执行

1. 用户消息进入系统后，发布 `USER_MESSAGE_RECEIVED` 事件。
2. `session_scheduler.py` 的 `on_event(...)` 在遇到 `user_message_received` 时只负责把事件入会话 mailbox，不直接触发 `WAKEUP_REQUESTED`。
3. `WAKEUP_REQUESTED` 的来源是：
   - `run_session(...)` 收尾时检测到 mailbox 仍有未消费事件，自动补发；
   - `heartbeat_watchdog` 周期扫描到“有 mailbox 事件、未 inflight、未 queued”的会话并补发。
4. `RuntimeSessionScheduler.run_session(session_id)` 被唤醒后消费 mailbox，选取 `task_type` 和 `enabled_groups`，调用 `process_background_turn(...)`。
5. `planner.process_turn(request: TurnRequest)` 做单步决策与执行。

### 3.2 单步推理与继续推进

当前 Planner 不再使用“单次唤醒内多轮 for-loop”模式，而是：
- 每次唤醒执行一次 LLM 推理。
- 若 LLM 返回纯文本：
  - 走 `_handle_text_fallback` 回写 session 事件；主会话文本投递由网关层 outbound 执行（仅主会话；子会话不直接发文本回调）。
  - 子会话同时标记终态并尝试触发屏障。
- 若 LLM 返回 Tool Calls：
  - 执行工具并写入 operation 事件。
  - 若 assistant 在 tool calls 旁边还返回了可见文本，这段文本也会先作为 assistant session message 持久化，避免历史回放丢失。
  - 若不是终止型工具，发布内部 follow-up 事件，触发下一次 `USER_MESSAGE_RECEIVED`，继续下一步。
  - 若 hook 在支持的 stage 上把 `force_finish=True`，则发布 `force_finish_requested` 并立刻结束当前 turn，不再 follow-up。

这个 follow-up 是单步架构稳定运行的关键，缺失时会出现“执行一个工具后卡住”。

### 3.3 心跳与漏唤醒保护

- `run_session` 期间启动会话级 heartbeat 任务，周期 touch `heartbeat_at`。
- 全局 `heartbeat_watchdog` 定期扫描：
  - 记录 stale 会话告警；
  - 对“有 mailbox 但未入队唤醒”的会话补发 `WAKEUP_REQUESTED`。

注意：watchdog 不会凭空推进没有 mailbox 的会话，因此工具后续推进必须通过 follow-up 事件或其他显式事件触发。

## 4. Sub-Agent 机制（当前实现）

### 4.1 委派模式

`delegate_task` 不再直接递归调用 Planner，而是：
- 创建子会话并写入初始 user 内容。
- 在 `global_state` 注册该子任务。
- 立即返回并发布 `USER_MESSAGE_RECEIVED` 到子会话（不等待 router LLM 选择），让子会话按统一事件链路运行。

这保证主/子执行模型一致，且主会话不会被子任务阻塞。

### 4.2 终态上报工具

`report_subtask_done` 由子 Agent 调用，写入终态状态与摘要到 `global_state`。

特性：
- 仅子会话应该可见；
- `record_event: false`，不写入普通 operation 历史；
- 幂等侧依赖 `mark_terminal`：已终态重复调用会返回 “already in terminal state”。

### 4.3 屏障聚合

当子任务终态变化时，Planner 会尝试 `_publish_subtask_barrier_if_ready`：
- 仅当“当前批次全部子任务终态”才发布最终聚合通知；
- 屏障标志在“聚合事件成功发布后”才置位，避免提前置位导致漏聚合；
- 通知内容汇总每个子任务状态与摘要，并发布 `SUBTASK_COMPLETED` 唤醒主会话继续回答用户。
- 失败路径保留“双通道”语义：失败可先即时通知一次，批次结束后再发最终聚合（并对同一子任务失败即时通知去重）。

## 5. 子任务状态管理与并发安全

`SessionSubTaskState` 关键行为：
- `register_task` 注册新任务；若当前批次已全部终态，自动切换到新批次编号。
- `mark_terminal` 原子地将任务转为 `completed/failed/cancelled`。
- `reopen_task` 可将已终态子任务恢复到 `running:<task_type>`（供 `message_subtask` 续跑）。
- `is_all_subtasks_terminal` 只判断当前批次是否终态完结。
- `get_aggregated_subtask_results` 仅聚合当前批次；`list_records()` 保留全历史可见性（供 `get_subtask_status`）。

近期修复：
- 不再清空旧 `records`，修复“`get_subtask_status` 看不到已完成子任务”回归。
- 通过批次号隔离聚合窗口，避免“新批次聚合混入旧批次摘要”。

并发说明：
- `get_subtask_status` 仅做内存读取，不持有互斥锁等待，不涉及嵌套 await 锁链，当前无死锁路径。

## 6. 能力组与工具可见性（已重构）

### 6.1 为什么要拆组

过去靠 `exclude_tools` 名称黑名单做主/子差异，可读性和稳定性都一般。现在改为：
- 主 Agent 专属组：`AgentTools`（含 `delegate_task` 等）。
- 子 Agent 专属组：`SubAgentTools`（含 `report_subtask_done`）。

### 6.2 当前可见性保证

在 Planner 中，会话级工具集由作用域开关控制：
- 主会话：仅启用 `always_enable_planner=true` 的能力组，不走 router。
- 子会话：启用 `always_enable_subagent=true` 基线组 + 委派路由得到的 `enabled_groups`。
- 委派路由仅在 `always_enable_subagent=false` 的候选组中做选择。

因此可见性不再依赖 prompt 运气，而是由能力字段与会话作用域约束。

### 6.3 能力启用与生命周期固定（新增）

- `always_enable_planner=true` 的能力组在主会话始终启用。
- `always_enable_subagent=true` 的能力组在子会话始终启用。
- 非 always-enable 组由路由结果决定是否启用。
- 子会话在首次调度前一次性完成路由并持久化 `enabled_groups`，后续 wakeup 复用该集合，不重复抖动路由。
- 仅对已启用能力组注册 Hook 和注入 tools schema。

### 6.4 Hook 协议对象化（新增）

- `before_llm_call` 接收 `BeforeLlmCallHookPayload`，核心字段为 `turn: LlmTurnContext` 与 `tools: list[LlmFunctionToolSpec]`。
- Hook 不应直接解析字符串历史；文本派生通过 `SessionMessageBlock`（及 body 子类型）实例方法完成。
- `before_llm_call` 读取历史语义时，应优先使用：
  - `turn.history_items` 读取完整 typed 历史
  - `turn.user_turn` 读取当前轮用户输入
  - `turn.llm_messages` 只在需要改写 provider request 时使用
- `after_llm_response` / `before_tool_call` / `after_tool_call` 使用结构化 payload（如 `LlmToolCallRequest`、`AfterToolCallHookPayload`），减少魔法 key。

### 6.5 CoreOperations 约束

- 当前实现中实际能力组名为 `CoreOperations`，不是 `CoreMessaging`。
- `CoreOperations` 默认不是 always-enable，包含文件系统、命令执行、会话文件读写、`send_file` 等执行工具。
- 主会话是否暴露该组取决于 `always_enable_planner`；子会话是否暴露该组取决于 `always_enable_subagent` 与委派路由结果。
- 文本/文件对话由 Planner 调用注入的 **GatewayEgress 接口** 输出（`emit_text` / `emit_file`），由 Business → WS Bridge → Gateway → Adapter 投递，不依赖 HTTP callback。
- Gateway 使用独立数据库路由表维护 `session -> channel` 与 `sub_session -> parent_session` 映射；Business 不再读写 `session.meta.gateway_route`。
- `send_file` 工具仅负责把本地文件落库为会话文件（生成 `file_id`）；Planner 本体不再硬编码 `send_file` 出站，文件回传由外层工具结果 egress 适配器统一处理。
- `send_file` 不会在会话消息历史里插入文件片段（避免 Planner 上下文出现 file segment / JSON 数组）；持久化的工具结果行会被脱敏为简短说明；用户侧仅通过 Gateway 渠道收到真实文件。
- 文件类 outbound 仅携带 `file_id`；adapter 负责按各自 channel 语义读取文件内容并发送。
- Gateway 与 Business 必须使用相同的 `DATABASE_URL`，否则网关侧 `gateway_session_routes` 无法解析会话路由，文件/文本将无法投递到 OneBot 等适配器。

### 6.6 脚本路径加载变更

`CapabilityRegistry` 对脚本路径调用 `.resolve()`，并按“组内就近实现”加载脚本。当前 `SubAgentTools/report_subtask_done` 脚本已放置在 `sub_agent_tools/scripts/` 下。

## 7. Typed History / IR 分层（新增）

当前系统将对话数据分为三层：

1. 持久化/仓储层数据
   - 数据库行、repository 返回值
   - 可以是裸 JSON / 行对象，但不应直接越过 `session/` 边界暴露给 planner
2. 系统内部 IR
   - `SessionMessageBlock`
   - `ToolCallRound`
   - `UserTurn`
   - `ChatHistoryItem`
   - 这是 hook 与 planner 应优先消费的语义对象
3. LLM 边界对象
   - `LlmChatMessage`
   - 由 IR 派生，用于最终 provider 请求
   - 不是业务 IR

当前边界规则：

- `MemoryProvider.get_history()` 直接返回 `list[ChatHistoryItem]`
- `TurnContextBuilder` 负责把当前轮 `user_turn` 从历史中拆出来
- `LlmMessageAssembler` 负责 `IR -> LlmChatMessage`
- `to_openai_messages(...)` 是最后一层 provider 序列化边界
## 8. 路由与调度策略（新）

### 7.1 主 Planner 路由策略

主 Planner（非子会话）不走工具组路由，工具组由 `always_enable_planner=true` 的能力组决定。

收益：
- 上下文稳定且更短，避免执行类工具污染 Planner；
- 纯编排角色清晰，不依赖提示词“自觉”。

### 7.2 子 Agent 路由策略

子 Agent 在“委派后首次调度前”进行一次路由：
- `delegate_task` 仅写入 baseline `enabled_groups`、`routing_pending`、`route_input` 并快速返回；
- `run_session` 检测 `routing_pending=true` 时调用 Router，写回最终 `enabled_groups` 并清除 pending；
- 首轮以及后续 wakeup 都复用已持久化的 `enabled_groups`；
- 路由失败时回退到“`always_enable_subagent=true` 的能力组集合”。

收益：
- 避免每次唤醒重复调用 Router；
- 子任务阶段工具集稳定，减少路由抖动；
- 去掉指纹/预算缓存逻辑，状态更直接。

## 9. Runtime Events 与兼容性要求

运行时事件分为两层：

- `user_message_received`
- `subtask_completed`
- `wakeup_requested`
- `file_upload_received`
- `force_finish_requested`
- 以及 manifest 声明的自定义字符串事件（例如 `my_custom_event`）

`fairyclaw/core/events/payloads.py` 中每个事件都对应显式 dataclass payload，并统一继承 `EventPayloadBase`：

- `session_id`
- `event_id`
- `source`
- `timestamp_ms`

说明：

- 旧的 `rag_index_requested` / `memory_compaction_requested` / `route_recompute_requested` 已移除
- 文件上传改为通过 `file_upload_received` 驱动 runtime hook
- Hook 的 rail-like 短路改为通过 `force_finish_requested` 驱动 runtime hook
- 自定义事件通过 `GenericRuntimeEventPayload` 暴露给 event hook；其中 `event_type`、`data`、`schema_definition` 都是显式字段
- event hooks 通过 `EventHookHandler` 消费 typed payload，而不是裸 `dict`
- 自定义事件只进入 `EventPluginDispatcher`，不会写入 mailbox、不会触发 wakeup、不会触发 Planner

`RuntimeEventEnvelope` 必须具备：
- `event_type` 字段；
- `payload` 字段；
- `type` 属性别名（返回 `event_type`）。

原因：
- `run_session` 在消费 mailbox 时通过 `event.type` 与 `event.payload` 解析 `task_type`；
- 缺失会触发事件处理异常并中断唤醒链路。

## 10. 开发新能力的标准方式

1. 在 `fairyclaw/capabilities/<group>/manifest.json` 定义能力组和 schema。
2. 在 `scripts/` 下实现 `async def execute(args, context)`。
3. `script` 默认按 `<group>/scripts/<name>.py` 加载，也可用可解析的相对路径复用。
4. 工具是否入历史由 `record_event` 控制。
5. 若工具仅用于子会话，不要放在主组；应通过 `always_enable_subagent` 与路由候选范围约束可见性。
6. 若能力组需要异步发布扩展事件，可在 manifest 顶层声明 `event_types`；支持简写字符串列表，也支持带 `description` / `schema` 的对象列表。
7. 对应 hook stage 形如 `event:<event_type>`；发布时调用 `publish_runtime_event("<event_type>", ...)` 即可。

### 10.1 上下文压缩与记忆能力组约定

- `compression_hooks`
  - 负责 token 预算控制，只压缩 `history_items`，再通过 `LlmMessageAssembler` 重建 `llm_messages`
  - 组内配置存放在 `compression_hooks/config.yaml`
- `rag_hooks`
  - 负责在 `before_llm_call` 阶段查询向量记忆并注入额外 system message
  - 向量存储逻辑必须内聚在能力组内部，不放到 core/infrastructure 的通用抽象层
  - 组内配置存放在 `rag_hooks/config.yaml`
- `memory_hooks`
  - `memory_pre_context` 负责注入会话摘要锚点
  - `memory_extraction` 负责在 `after_llm_response` 阶段提取长期事实并写入 `rag_chunks`
  - 首期 `memory_extraction` 采用规则提取（heuristic），不是 LLM 提取
  - 组内配置存放在 `memory_hooks/config.yaml`
- 模型类配置统一放在 `config/llm_endpoints.yaml`
  - `embedding` profile 用于本地嵌入模型
  - `compaction_summarizer` profile 用于摘要生成
- 除已有通用项外，不要再把能力组专属参数加进 `fairyclaw.env` / `settings.py`

## 11. 修改核心时必须遵守的规则

- 异步优先：I/O、DB、LLM、事件发布必须 `async/await`。
- 不打破双轨事件语义：用户可见对话写 `session`，工具调用写 `operation`。
- 不绕过事件链：避免直接从工具内部强行驱动 Planner 递归执行。
- 不让子任务结果跨批污染：任何批次聚合逻辑都必须有清晰边界。
- 不把子会话工具暴露给主会话（反之亦然）。
- 网关接口统一鉴权：`Sessions` / `Chat` / `Files` 均需 `require_auth`。
- 文件读取接口必须做会话作用域校验：`file_id` 查询需携带并校验 `session_id`（由 Gateway 通过 WS file_get 代理到 Business）。
- 不要让 hook payload 退化成 `dict`；stage payload 必须保持明确 typed object。
- 不要再引入 `from_legacy(...)` 风格的第一版兼容入口。

## 12. 最近关键修复与行为变化汇总

1. 修复 `RuntimeEventEnvelope` 缺字段导致的 `wakeup_requested` 处理异常。
2. 增加工具执行后的内部 follow-up 事件，解决“执行一个工具后卡住”。
3. 完成 Sub-Agent 屏障聚合链路，主会话在全部子任务终态后统一唤醒。
4. 修复子任务批次串扰，避免新问题混入旧任务摘要。
5. 拆分能力组为 `AgentTools` 与 `SubAgentTools`，实现真正的主/子工具隔离。
6. 主 Planner 改为“always_enable_planner 自动启用”，子 Agent 改为“委派快速返回 + 首次调度前一次路由 + 后续复用 + always_enable_subagent 基线”。
7. 重写 Planner 系统提示词，明确“委派是默认且必选路径”。
8. 执行能力组名称统一为 `CoreOperations`，由子会话按路由结果按需启用。
9. 引入五阶段 Hook 管线与 typed payload 协议，支持能力系统侵入 prompt 构建、检索、压缩、路由与工具后处理。
10. `MemoryProvider` 收紧为 typed IR 边界：`get_history()` 返回 `ChatHistoryItem`，`query_memory` 已删除。
11. `TurnRequest.from_legacy(...)` 已删除，调用方统一直接构造 typed `TurnRequest`。
12. `Planner` 主/子差异已抽到 `turn_policy.py`，降低 `_is_sub_session(...)` 分支扩散。
13. `TurnContextBuilder` 现在会显式提取 `user_turn`，不再让“当前轮用户输入”只隐含在 `history_items` 末尾。
14. `LlmMessageAssembler` 会把连续 tool rounds 聚合回单条 assistant tool-call message，并保留伴随 tool calls 的 assistant 文本。
15. 核心会话调度事件由 `core/events/session_scheduler.py` 处理；扩展类运行时事件通过插件执行器分发（`event:*` hook stage）。
16. 上下文压缩改为预算驱动的四级渐进式压缩：大项截断、近期窗口保留、工具历史剔除、紧急裁剪。
17. `memory_pre_context` 会按需生成/读取 `memory_compactions` 摘要，并以额外 system message 注入上下文。
18. `memory_extraction` 已接入 `after_llm_response` 阶段，首期使用规则提取并写入 `rag_chunks` + 本地向量库。
19. `rag_retrieval` 已接入本地向量检索，按会话作用域召回记忆事实后注入 prompt。
20. 支持 Hook 通过 `force_finish` 静默短路当前 turn：运行时发布 `force_finish_requested`，不触发 follow-up，也不进入下一轮推理。
21. 支持能力组声明自定义 runtime event：事件类型允许 `EventType | str`，scheduler 仅将核心三事件送入 mailbox/wakeup，其他声明型事件统一走 `EventPluginDispatcher`。

## 14. 网关层（Gateway）与业务层（Business）分进程边界（新增）

当前系统对外入口已迁移到独立 Gateway 进程，Business 进程只负责运行时与规划：

- **Business 进程**：`SessionEventBus` / `RuntimeSessionScheduler` / `Planner` / `PersistentMemory` / DB
  - 只对外暴露：`/healthz` + 内部 `WebSocket Bridge`（默认 `GET /internal/gateway/ws`）
- **Gateway 进程**：对外 HTTP API + OneBot 适配
  - 对外暴露：`/v1/sessions` / `/v1/sessions/{session_id}/chat` / `/v1/files` / `/v1/files/{file_id}` 等
  - 通过 `WsBridgeClient` 与 Business 通信，协议见 `docs/GATEWAY_ENVELOPE.md`

重要约束：

- Business 进程不再实现 `callback_url` 与任何外部 HTTP 回调投递。
- Business 进程不再暴露对外 `Chat/Files/Sessions` HTTP API；这些属于 Gateway。

## 13. 给 AI 助手的操作建议

- 先确认当前会话是主会话还是子会话，再判断工具可见性问题。
- 排查“卡住”优先检查三件事：是否有 follow-up 事件、mailbox 是否有事件、wakeup_queued/inflight 状态是否一致。
- 排查“重复聚合”优先检查 `global_state` 批次边界是否重置。
- 排查“router 成本高”优先检查是否只在子会话首次调度前路由一次，以及 `enabled_groups` 持久化是否正确。
- 排查 `before_llm_call` 行为时，优先区分三层：
  - `history_items` 是否正确
  - `user_turn` 是否正确
  - `llm_messages` 是否只是 IR 的正确派生
- 修改核心调度后务必进行 `python3 -m compileall fairyclaw` 校验，并做最小复现脚本验证事件链完整性。
