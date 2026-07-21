# Subagent Delegation Recovery Design

## Goal

把 Hermes 子 Agent 委派从“成功返回 / 失败结束”增强为可观察、可恢复、可兜底的执行链路，覆盖超时、网络/传输异常、空响应、工具失败和迭代耗尽等常见失败。

## Scope

本次只修改现有 `delegate_task` 执行路径、相关测试和委派文档。不会加入定时调度、联网搜索、Smart Routing、Always-on Executor、Auto-Evolve 或自动清理等其他架构能力。

## Design

### 1. 状态与结果协议

保留现有结果字段和现有调用接口，并补充稳定的恢复字段：

- `status`: 继续兼容现有 `completed`、`failed`、`timeout`、`interrupted`，恢复成功使用 `fallback_succeeded`，所有尝试结束仍失败使用 `final_failed`。
- `failure_class`: `soft`、`hard` 或 `none`。
- `attempts`: 实际执行次数。
- `attempt_history`: 每次尝试的状态、失败分类、退出原因、错误摘要和耗时。
- `fallback`: 结构化的失败说明和下一步建议；不伪装成正常成功结果。

内部状态迁移为 `pending -> running -> (succeeded | soft_failed -> retrying -> running | hard_failed | fallback_succeeded | final_failed)`。中断不重试。

### 2. 失败分类

分类采用确定性规则，不调用模型：

- 软失败：超时、网络/连接/限流异常、空响应、临时工具错误。
- 硬失败：参数或逻辑错误、工具不存在/明确不可用、达到 `max_iterations`、中断和未知但不可安全重试的异常。

分类器接收已有结果或异常，返回 `(failure_class, reason_code)`，并保留原始错误文本的截断摘要。

### 3. 有限重试与退避

新增 `delegation.max_retries`（默认 `2`）和 `delegation.retry_backoff_seconds`（默认 `1.0`）配置读取函数；重试次数只对软失败生效。每次等待使用有上限的指数退避：`base * 2 ** (attempt - 1)`，并允许测试注入/配置为 `0`。

重试使用同一目标和上下文，重新创建子 Agent，以避免复用已处于异常状态的会话；批量委派仍受现有并发上限约束。重试期间发送现有进度事件，并把每次尝试加入历史。

### 4. 兜底结果

当软失败耗尽重试或发生硬失败时，返回 `final_failed`，并在 `fallback` 中提供机器可读的 `reason_code`、`failure_class`、`message` 和 `next_action`。如果已有可用的部分输出，则返回 `fallback_succeeded` 和该部分输出，同时明确标注其为降级结果。

### 5. 验证

补充单元测试覆盖：软失败重试后成功、软失败耗尽、硬失败不重试、空响应分类、迭代耗尽分类、退避参数、结果历史和兜底字段；运行现有 delegate 工具测试集和本次相关测试。

## Compatibility and Safety

- 不改变 `delegate_task` 的入参 schema；重试策略由用户配置控制，不由模型调用参数控制。
- 默认重试仅增加软失败恢复能力；硬失败、中断和超过预算不会被无限重试。
- 现有状态字段、摘要预算、成本统计、TUI 事件和超时诊断路径继续保留。
- 失败永远以明确状态返回，不把兜底说明当作正常成功摘要。
