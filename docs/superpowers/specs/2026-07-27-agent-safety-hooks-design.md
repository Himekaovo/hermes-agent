# Agent Safety Hooks Design

## Goal

把《给 Agent 加了 11 个》中的“进门安检、出门检查、散场归档”落到 MyHermes 的现有 Hook 生命周期，让每轮 Agent 执行都有可审计、可测试、可隔离的安全检查，同时不重写 Agent 主循环。

## Scope

本次实现只覆盖本地 Hermes Agent runtime：

- `pre_llm_call`：模型调用前的进门安检；
- `post_llm_call`：模型调用后的出门检查；
- `on_session_end`：会话或 turn 结束时的散场归档。

不接入外部安全服务、联网扫描、后台调度、模型二次审查或自动修改生产代码。

现有全局 Hook、实例级 Hook Override、Cron job Hook 和 Subagent Hook 继承机制继续保留。内置安全 Hook 作为实例级 callback 组装，避免把一个 Agent 的会话状态写入全局注册表。

## Design Principles

1. **Deterministic**：同一 payload 得到相同检查结果，不依赖模型调用。
2. **Fail-open by default**：普通审计异常只记录日志并继续执行。
3. **Fail-closed for explicit violations**：明确的身份缺失、危险路径、越权委派、敏感信息外泄和恶意提示注入可以阻断对应阶段。
4. **Structured results**：每个职责返回统一的安全结果，不把审计信息拼接成不可解析文本。
5. **Single write authority**：安全 Hook 不直接写 `MEMORY.md`、`USER.md` 或 Skill 文件；所有记忆写入继续经过 Memory Governance。
6. **Payload compatibility**：全局 Hook 和实例 Hook 看到相同的标准化 payload，包括 `telemetry_schema_version`。

## Architecture

新增 `agent/safety_hooks.py`，只依赖标准库和现有纯函数接口。它提供：

```python
def build_safety_hook_overrides(
    *,
    config: Optional[Mapping[str, Any]] = None,
    memory_provider: Optional[Any] = None,
) -> Dict[str, List[Callable[..., Any]]]

def run_safety_checks(
    event: str,
    payload: Mapping[str, Any],
    *,
    config: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]
```

`build_safety_hook_overrides()` 返回的 callback 列表由 Agent 构造阶段复制并绑定到该 Agent。Hook callback 不保存跨 Agent 的可变全局状态；需要会话状态时，只从当前 payload 或显式传入的 session-local object 读取。

`agent/agent_init.py` 在构造 Agent 时把内置安全 Hook 与显式实例 Hook 合并，顺序固定为：

```text
global plugins → built-in safety hooks → instance override hooks
```

现有模块级 `invoke_hook()` 负责标准化 payload 和隔离异常；安全 Hook 不绕过该入口。

## The 11 Responsibilities

### Pre-LLM: ingress gate

1. **identity-hook**
   - 确认 session、sender、agent identity 和 profile scope 可用；
   - 将必要的身份上下文作为结构化 ephemeral context 返回；
   - 不把凭证、token 或完整环境变量注入模型。

2. **pm-mode-detector**
   - 识别 planning、execution、review 和 recovery 等运行模式；
   - 输出 `mode` 和 `confidence` 元数据；
   - 不根据模式自动修改工具权限，只提供给后续安全检查使用。

3. **subagent-checklist**
   - 检查委派目标、任务描述、继承的 Hook 列表和 session scope；
   - 拒绝缺少 parent/session 关联或越过允许范围的委派；
   - 不自动创建新的 Subagent 或修改委派内容。

4. **wiki-recall**
   - 从本地 SkillWiki、holographic memory 或现有只读 provider 获取与当前任务相关的安全提示；
   - 只返回有界的 context，不安装 Skill、不升级 provenance、不写记忆；
   - 遵守现有 SkillWiki 的 pinned revision 和本地修改保护。

5. **context-propagation**
   - 检查 `session_id`、`task_id`、`turn_id`、profile 和 telemetry schema 是否一致；
   - 发现跨 Agent 或跨 profile 泄漏时返回阻断结果；
   - 不修改调用方的原始 payload。

6. **security-inspector**
   - 对用户输入、历史上下文和待注入 Hook context 执行确定性检查；
   - 检查 prompt injection、外泄指令、危险路径、凭证样式和不受信任的工具指令；
   - 明确高风险结果阻断当前 LLM 调用，普通命中只告警。

### Post-LLM: egress gate

7. **response-and-action-auditor**
   - 检查 assistant response、工具调用轨迹、导入路径、文件写入目标和外泄模式；
   - 对危险路径、未经批准的写入或敏感信息输出返回结构化阻断结果；
   - 不把失败响应伪装成成功，不自动重试模型调用。

### Session end: archival gate

8. **session-summary**
   - 生成有界的会话摘要和安全状态，不保存完整 secret 或原始凭证；
   - 只使用当前 session 的消息快照。

9. **incident-journal**
   - 记录阻断、异常、工具失败和恢复事件；
   - 记录 reason code、风险级别、session/task/turn 标识和时间；
   - 日志写入失败不得阻断 session cleanup。

10. **memory-commit-guard**
    - 在会话结束前检查待提交的学习或记忆变更是否经过 Memory Governance；
    - 未经过 governance 的候选只报告，不直接写入；
    - 不自动批准、合并、删除或回滚记忆。

11. **session-cleanup**
    - 清理 session-local Hook 状态、临时文件和 spill 记录；
    - 通过 `finally` 和现有生命周期调用保证异常时也执行；
    - 不清理用户明确保留的版本快照或 provenance 数据。

## Result Contract

每个职责返回一个或多个如下结构的结果：

```python
{
    "hook": "security-inspector",
    "event": "pre_llm_call",
    "action": "allow",  # allow | warn | block | skip
    "reason_code": "prompt_injection_detected",
    "risk_level": "high",  # low | medium | high
    "message": "Human-readable explanation",
    "metadata": {
        "session_id": "...",
        "task_id": "...",
        "turn_id": "...",
    },
}
```

结果必须 JSON-serializable。`message` 不得包含未经脱敏的 token、密码、完整环境变量或原始秘密内容。

阶段行为：

- `pre_llm_call` 的 `block` 阻止当前模型调用，并返回可解释错误；
- `post_llm_call` 的 `block` 阻止危险响应进入用户可见输出，但保留审计记录；
- `on_session_end` 的任何 Hook 结果都不能阻止清理流程；
- 单个 callback 异常由现有 Hook 隔离逻辑捕获，并降级为 warning。

## Configuration

配置保持本地 profile scope，默认启用安全检查：

```yaml
agent:
  safety_hooks:
    enabled: true
    block_high_risk: true
    max_context_chars: 12000
    audit_path: "$HERMES_HOME/logs/safety-hooks.jsonl"
```

未配置时使用安全默认值。配置错误不应导致 Hermes 启动失败，而应回退到默认配置并记录 warning。

## Integration Points

计划修改：

- `agent/safety_hooks.py`：新增安全职责和结果模型；
- `agent/agent_init.py`：构造内置 Hook override，并与用户实例 Hook 合并；
- `agent/turn_context.py`：验证 pre-LLM block 与有界 context 注入；
- `agent/turn_finalizer.py`：消费 post-LLM audit 结果，并保证 session-end cleanup；
- `tools/delegate_tool.py`：校验 Subagent 的安全 Hook 继承和 scope；
- `tests/agent/test_safety_hooks.py`：纯函数与职责测试；
- `tests/run_agent/test_safety_hook_lifecycle.py`：三阶段集成测试；
- `tests/tools/test_delegate_hook_overrides.py`：委派隔离与继承测试；
- `docs/agent-safety-hooks.md`：用户与开发者说明。

不修改现有 PluginManager 的全局 Hook 契约，不新增外部依赖，不改变 Memory Provider 的公共 API。

## Testing Strategy

测试先行，覆盖：

- 每个职责的 allow/warn/block/skip 结果；
- 高风险 pre-LLM 结果确实阻止模型调用；
- post-LLM 阶段不会泄漏危险响应；
- session-end 清理在正常、异常和中断路径都执行；
- Hook callback 异常不会破坏主 Agent loop；
- 全局、内置和实例 Hook 的顺序；
- `telemetry_schema_version` payload 一致性；
- Cron 和 Subagent 的 callback 列表不共享；
- profile A 的审计和 context 不会进入 profile B；
- Memory Governance 仍是唯一记忆写入闸门；
- audit JSONL 的 malformed line 保留与并发写入安全；
- context 输出有界且 secrets 被脱敏。

Focused checks：

```bash
pytest tests/agent/test_safety_hooks.py -q
pytest tests/run_agent/test_safety_hook_lifecycle.py \
       tests/tools/test_delegate_hook_overrides.py \
       tests/test_instance_hook_overrides.py -q
git diff --check
./.venv/bin/python -m compileall -q agent tools
```

## Compatibility and Rollout

- 旧的全局 Hook 继续按原顺序执行；
- 新安全 Hook 默认作为实例级 callback 追加；
- 没有安全结果的旧 callback 不需要迁移；
- 安全 Hook 初始化失败时不阻止 Hermes 启动，但会记录诊断；
- `main` 合并前必须完成 focused tests 和完整相关回归；
- 本次不引入自动升级、自动修复或自动合并行为。

## Open Decisions Resolved

- 采用现有 Hook 生命周期，不新建第二套事件总线；
- 采用 deterministic checks，不调用模型做二次安全判断；
- 采用实例级 Hook 绑定，保留全局兼容层；
- 采用“高风险显式阻断、普通异常 fail-open、session cleanup 永不阻断”的错误策略；
- 记忆和 Skill 的写入继续分别由 Memory Governance 与 SkillWiki provenance 负责。
