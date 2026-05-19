# DebugMind Phase 2 — 生产硬骨头

> 这是 **Phase 2 工作单**。Phase 1（见 REFACTOR_PLAN.md）已完成；本阶段把项目从"能跑"提升到"敢上生产"。
> 写作风格沿用 REFACTOR_PLAN：契约严格，内部宽松；按编号顺序做，不要跳号。

---

## 0. 给执行 AI 的工作纪律

1. **从基线开始**。先 `pytest -v` 确认 122 全绿、`debug-mind eval --search-only` 跑出 hit@1=0.92。把数字写进执行日志第一行。
2. **一次一个任务**。每个子任务 4.x 做完都要：跑测试 → 跑 eval（仅 search-only，无 API key）→ 在执行日志追加一行 → 再开下一个。
3. **不要跳到 Phase 3 / 4**。Phase 2 全部通过后停下来等评审。
4. **不要碰 Phase 1 的成果**：BugCase schema、`tools/schemas.py`、`evaluation/`、`docs/embeddings.md` 都不许改既有字段含义（可加新字段）。
5. **不要重写 README**（demo / 架构图属于 Phase 3）。允许追加章节，不允许删既有内容。
6. **不要引入重依赖**。允许新增 `filelock`、`tenacity`、`structlog`、`opentelemetry-api`（仅 API 不含 SDK）这种小包；不允许引入完整 OTel SDK + 后端依赖（让用户选）。
7. **不要"顺手升级" Anthropic 模型 ID**。Phase 4 才动 provider 抽象。
8. **不要破坏旧 markdown 解析**。所有审计 / 一致性新增字段，旧 case 缺字段必须容忍。
9. **每个新增模块顶部写 docstring**，说明它为什么存在。
10. **遇到设计模糊点**，挑"更简单、可回滚"的那个，把取舍写进执行日志，不要阻塞。

---

## 1. 背景与目标

Phase 1 让 DebugMind 的"记忆质量"上了一个台阶，但生产场景下还有 7 件事会立刻爆雷：

- 多进程同时写 `memory/` 会损坏文件 / 索引（并发 = 0）
- ReAct 主循环没有 token 预算，能无限烧钱
- 没有结构化日志 / trace，线上错诊根本没法复盘
- API 429 / 5xx 直接挂掉，记忆也丢
- MCP 没鉴权，谁都能 `verify --wrong` 把库洗了
- `error_log` 整段塞 prompt，既是 injection 入口又是 token 炸弹
- `verify(correct=False)` + Ctrl+C 留下"vector 删了、markdown 没改名"的孤儿

Phase 2 解决这七件事 + 一个补 hit_count 进 ranking 的实验性增强。**不做** OTel exporter 全套（只留 hook）、不做 LLM provider 抽象（Phase 4）、不做 Web UI（Phase 4）。

---

## 2. 全局约束

| 约束 | 说明 |
|------|------|
| 已有 122 测试必须全绿 | 通过数不准下降 |
| `debug-mind eval --search-only` 必须仍 hit@1 ≥ 0.92 | 任何记忆侧改动都要回归 eval |
| 默认行为零变化 | 不设新环境变量时，CLI 输出、API 行为应与 Phase 1 完全一致 |
| 不切换持久层 | 仍 ChromaDB + Markdown；SQLite 留 Phase 4 |
| 中文 OK，标识符英文 | 同 REFACTOR_PLAN |
| 不写无关注释 | 仅在"为什么"非显而易见时加注释 |

---

## 3. 改动前基线（必填）

执行日志第一行：

```
[BASELINE] pytest 通过 122 / 失败 0 / 跳过 0；eval search-only hit@1=0.92 MRR=0.96；ripgrep 可用
```

---

## 4. Phase 2 任务

> 顺序：2.1 → 2.2 → 2.3 → 2.4 → 2.5 → 2.6 → 2.7 → 2.8。**2.7（reconciliation）依赖 2.1（锁），最后做**。

### 4.1 Task P2-1 — 并发安全（文件锁 + chroma 多进程友好）

**Why**：两个 `debug-mind diagnose` 同时跑会导致 markdown 半写、`.tmp` 残留、chroma SQLite locked。多人共用 `memory/` 时第一周就会出 issue。

**改什么**

1. 新增依赖：`filelock>=3.12`（加到 `[project.dependencies]`）。
2. `MemoryStore.__init__` 时创建 `memory/.lock` 文件，所有写操作（`save` / `verify` / `mark_used` / `delete` / `rebuild_index`）外层包：
   ```python
   from filelock import FileLock
   with FileLock(str(self.memory_dir / ".lock"), timeout=30):
       ...
   ```
3. **读不加锁**（`get` / `search` / `list_recent` / `stats`），但读时如果命中的 markdown 不存在（被并发删了）就忽略。
4. 锁超时（30 秒）抛 `MemoryBusyError`，CLI 捕获后打印友好提示而不是堆栈。
5. chroma 并发：现在 `chromadb.PersistentClient` 在写操作上已有内部锁，但要测一下两进程同时 `upsert` 不会崩。可以接受 fail-fast（用文件锁兜底）。

**验收**

- [ ] `tests/test_concurrency.py` 新增：fork 10 个子进程并发 `save()`，最后所有 case 都能 `get()` 到，没有 `.tmp` 残留
- [ ] 锁超时单测：mock filelock 抛 `Timeout`，CLI 退出码 != 0 且打印"memory busy"
- [ ] `pytest -v` 全绿，eval 数字不变
- [ ] 执行日志记录：你做了 30 秒 timeout 还是其它值

**不要做**

- 不要把锁加在 read 路径上（会让 `serve` MCP server 堵死自己）
- 不要为了避锁去拆 markdown 目录（让两个进程各自一目录又破坏共享语义）

---

### 4.2 Task P2-2 — Token / 成本预算

**Why**：max_turns=20 × 每轮 LLM call × 可能调 LLMReranker × Anthropic 没退款。一个无限循环诊断 = 一杯咖啡钱。

**改什么**

1. 新模块 `src/debug_mind/agent/budget.py`：
   ```python
   class TokenBudget:
       def __init__(self, max_input_tokens=200_000, max_output_tokens=20_000, max_cost_usd=0.50, model="claude-sonnet-4-20250514"):
           ...
       def record(self, usage: anthropic.types.Usage) -> None: ...
       def remaining_tokens(self) -> tuple[int, int]: ...
       def remaining_cost(self) -> float: ...
       def is_exceeded(self) -> tuple[bool, str | None]: ...
   ```
2. 价格表（USD per 1M token）至少含当前默认模型的 input / output / cache_read / cache_write 四档，从环境变量 `DEBUG_MIND_PRICING_JSON` 可覆盖。
3. `DiagnosticAgent.__init__` 接 `budget: TokenBudget | None = None`；`_run_loop` 每轮 `response.usage` 喂给 budget，循环顶部判 `is_exceeded()` 真就 break，并把原因写进 final reasoning。
4. CLI 新增：
   ```
   --max-cost 0.5         默认 0.5 美元
   --max-tokens 50000     默认 50k token
   ```
   也可用 `DEBUG_MIND_MAX_COST` / `DEBUG_MIND_MAX_TOKENS`。
5. budget exceeded 时仍要尝试保存"部分诊断"——把当前已收集的 root_cause / steps 落盘（即使不完整），用 `BugStatus.UNRESOLVED`。

**验收**

- [ ] 单测：mock `response.usage` 让 budget 提前耗尽，验证循环在该轮后退出且 reasoning 含 "budget exceeded"
- [ ] 单测：budget 没耗尽时行为不变
- [ ] CLI `--max-cost 0` 立刻退出并打印原因，不是崩
- [ ] 执行日志：附一组实际跑过的"高 budget vs 低 budget"对比（哪怕用 mock client）

**不要做**

- 不要把价格写死成"未来肯定要改"的常量；至少要从 dict + env 读
- 不要在 budget 触发时直接 raise 而不落盘——这会"白花了钱什么也没存"

---

### 4.3 Task P2-3 — 结构化日志（含 OTel hook，不含 SDK）

**Why**：线上错诊复盘只能靠日志。`print` / `console.print` 不能机器解析。

**改什么**

1. 新模块 `src/debug_mind/observability/logger.py`：
   ```python
   def get_logger(name: str) -> Logger: ...
   # 内部：根据 DEBUG_MIND_LOG_FORMAT=json|text 切换 handler
   # JSON 格式至少含：timestamp, level, logger, msg, trace_id, case_id, model, tokens_in, tokens_out, latency_ms
   ```
2. 给 `DiagnosticAgent.diagnose()` 加一个隐式 `trace_id = uuid4().hex[:16]`，所有 log line / save 的 case 都打这个 id。
3. 每个 tool call 一条 INFO log：tool name、input 摘要（截断 200 字符）、result.found / saved 信号、耗时。
4. 给 `MemoryStore.save` / `verify` / `mark_used` / `delete` 各加一条 INFO log。
5. **OTel hook（仅接口，不强依赖）**：如果环境里能 `import opentelemetry.trace`，则把 trace_id 作为 span attribute；不能 import 就跳过。`opentelemetry-api` 加到 `[project.optional-dependencies].observability`，不进默认依赖。
6. 默认 LOG 输出到 stderr；CLI 默认仍走 rich console（不变），但环境变量 `DEBUG_MIND_LOG_FILE=path` 时落盘 jsonl。

**验收**

- [ ] 单测：`DEBUG_MIND_LOG_FORMAT=json` 时，capture stderr 验证至少包含 trace_id 字段
- [ ] 单测：未设环境变量时 CLI 输出和 Phase 1 字面一致（snapshot 比对 `debug-mind list` 或 `stats` 的输出）
- [ ] 没装 `opentelemetry-api` 时整个流程不崩
- [ ] 执行日志：贴一条实际的 JSON log 截图（脱敏）

**不要做**

- 不要把 rich console 的 markup 标签也写进 jsonl（jsonl 里只放纯 message）
- 不要把 OTel exporter / SDK 强制依赖

---

### 4.4 Task P2-4 — API 重试 + 部分诊断兜底

**Why**：Anthropic 429 / 5xx / 网络抖动 → 现在直接挂；记忆也没存。

**改什么**

1. 新增依赖：`tenacity>=8.2`。
2. 包一层 `_call_anthropic()` 私有方法，对 `anthropic.RateLimitError` / `anthropic.APIStatusError` (5xx) / `anthropic.APIConnectionError` 做指数退避：最多 3 次，base 2 秒，jitter 0.5。其它错误（400 / 401 / 403）不重试，直接抛。
3. 主循环 `except anthropic.APIError` 块改成：调用 `_call_anthropic` 已经包了 retry；如果它最终还是失败，记 log，**把当前已知信息（symptoms / similar_case_ids / 已 read 的 file 路径）兜底存成 `BugStatus.UNRESOLVED` 的 case**，返回 confidence=0.0 的 DiagnosisResult。
4. CLI `--no-retry` flag 提供测试用 / 调试用关闭重试。

**验收**

- [ ] 单测：mock client.messages.create 前两次抛 RateLimitError、第三次成功，验证最终成功且重试日志可见
- [ ] 单测：mock 三次都抛 5xx，验证最终返回 UNRESOLVED case，markdown 落盘
- [ ] 401 不重试（节省 token）：mock 抛 AuthenticationError，立即返回

**不要做**

- 不要把重试用 `time.sleep(...)` 硬写，必须用 tenacity 的 wait/stop 策略（方便测试 patch）
- 不要重试 400 系列错误

---

### 4.5 Task P2-5 — MCP 鉴权 + 审计日志

**Why**：MCP server 默认对所有 stdio client 开放。生产场景任何能跑 `debug-mind serve` 的进程都能 `verify_bug_case <id> --wrong` 删数据。

**改什么**

1. `mcp_server.py` 顶部读环境变量 `DEBUG_MIND_MCP_TOKEN`。如果设了：
   - 写类工具（`save_bug_case` / `verify_bug_case` / `delete_bug_case`）的 FastMCP wrapper 加一个 `_auth_token: str` 必传参数；server 启动时校验。
   - 读类工具（`search_similar_bugs` / `list_recent_bugs` / `get_bug_stats`）不要求 token。
   - 不一致返回 `{"error": "auth_required"}`。
   - 如果环境变量没设：server 启动时打印 WARNING 但保持开放（避免 break 现有用户）。
2. 审计日志：所有写操作（无论从 CLI 还是 MCP）追加一行到 `memory/audit.jsonl`：
   ```json
   {"ts": "...", "actor": "cli" | "mcp", "op": "save|verify|delete|mark_used", "case_id": "...", "details": {...}}
   ```
3. CLI 破坏性操作（`delete` / `verify --wrong`）保留现有 confirm；MCP 端不能 confirm，但必须写审计日志。
4. 新 CLI 子命令：`debug-mind audit [--since=24h] [--op=delete]` 倒序显示审计记录。

**验收**

- [ ] 单测：设了 `DEBUG_MIND_MCP_TOKEN` 后，不带 token 调写工具返回 error；带正确 token 通过
- [ ] 单测：不设环境变量时 server 启动 WARNING 但调用通过
- [ ] 单测：`save` / `verify --correct` / `verify --wrong` / `delete` 都在 audit.jsonl 留痕
- [ ] `audit` CLI 能按 --op 过滤

**不要做**

- 不要把 token hash 后存仓库；token 只在 env，不写文件
- 不要做完整 RBAC（角色、用户、权限矩阵）——本轮就一个 token

---

### 4.6 Task P2-6 — 输入消毒 + 大小限制

**Why**：`error_log` 整段进 system prompt = prompt injection 入口 + token 炸弹。

**改什么**

1. 在 `agent.py:_build_user_message` 之前，加 sanitization：
   - `bug_description` 截断到 4 KB，超长截断 + 加 `... [truncated]` 标记
   - `error_log` 截断到 16 KB，截断保留**头 8 KB + 尾 8 KB**（栈底信息往往最重要）
   - `environment` value 截断到 256 字符 each，键数最多 20
   - 移除 ASCII 控制字符（保留 `\n` / `\t`）
2. `BugCase` 同样在 `save()` 入口做一遍（防止 MCP 直接传超长内容）。
3. tags 数量限制：最多 20 个，超过部分丢弃 + log WARNING。

**验收**

- [ ] 单测：传 100 KB error_log → 实际进 prompt 的部分 ≤ 16 KB 且包含原文末尾
- [ ] 单测：传 30 个 tag → save 后 case.tags ≤ 20
- [ ] 单测：控制字符 \x00 \x01 \x1f 被剥除
- [ ] eval 数字不退化（截断不应影响 12 个 benchmark case 的召回）

**不要做**

- 不要把截断阈值写死成常量；从 env 可调（`DEBUG_MIND_MAX_LOG_SIZE` 等）
- 不要剥除 `\n` `\r` `\t`——栈追踪需要它们

---

### 4.7 Task P2-7 — 一致性 reconciliation（依赖 P2-1 的锁）

**Why**：`save` 是"先 markdown 再 vector"，`verify(correct=False)` 是"先 rename markdown 再 delete vector"。任何一步被 Ctrl+C 都会留孤儿。

**改什么**

1. `MemoryStore.__init__` 末尾跑一遍轻量 reconciliation：
   - 扫 `cases_dir/*.md.tmp`：超过 10 分钟没动的删掉，新的留（可能是并发写中）
   - 不做重 IO（不要每次启动都重 embed）
2. 新 CLI：`debug-mind doctor`，重逻辑都在这里：
   - 列出 markdown 存在但 vector 中无的 id（"missing vectors"）
   - 列出 vector 中存在但 markdown 没有的 id（"orphan vectors"）
   - `--fix`：missing vectors 调 `_save_to_vector` 补；orphan vectors **不自动删**，只打印让用户决定（可能是别的工具写的）
   - `--fix --delete-orphans`：才真删
3. `verify(correct=False)` 改成事务化：先把 markdown rename 成 `.rejected.pending`，再 delete vector，最后 rename 到 `.rejected`。中途崩了下次 `doctor` 能识别 `.pending` 状态并完成。

**验收**

- [ ] 单测：手造一个 markdown 但 vector 缺失的状态，`doctor --fix` 后能 search 到
- [ ] 单测：手造一个 vector 但 markdown 缺失，`doctor` 不动它，`doctor --fix --delete-orphans` 才删
- [ ] 单测：模拟 `verify(correct=False)` 在 rename 后崩溃，`doctor --fix` 能完成 rename
- [ ] `pytest -v` 全绿

**不要做**

- 不要在 `__init__` 里跑重 IO（用户每次启动都等 30 秒会骂街）
- 不要在 doctor 里默认删数据——只读 / 列出 / 提示

---

### 4.8 Task P2-8 — hit_count 参与 ranking（实验性）

**Why**：Phase 1 已经收集了 `hit_count` 和 `last_used_at`，但 ranking 没用。被反复采用的 case 应该排前。

**改什么**

1. `MemoryStore.search` 内排序改成：
   ```python
   effective = score * (1.0 if verified else 0.7) * (1 + math.log1p(hit_count) * 0.05)
   ```
   即 verified 提升、被采用次数对数衰减加权。
2. 系数 `0.05` 从 env `DEBUG_MIND_HIT_COUNT_WEIGHT` 可调。
3. 跑一遍 `debug-mind eval --search-only`，**eval 数字必须不退化**——这是硬约束。如果实验下来 hit@1 跌了，把系数调成 0 默认，把"实验性"标进 docstring + 执行日志。

**验收**

- [ ] 单测：相同 score、不同 hit_count，hit_count 高的排前
- [ ] eval 数字：附 baseline vs after 对比到执行日志
- [ ] 默认行为可控：`DEBUG_MIND_HIT_COUNT_WEIGHT=0` 时退化为 Phase 1 行为

**不要做**

- 不要为了让数字好看在 benchmark 上 hit_count 注水
- 不要把权重写死

---

## 5. Phase 3 / 4 / 5 占位（本轮不做）

- Phase 3 = 开源工程化（CI / PyPI / 文档 / demo）→ 见 `PHASE_3_PLAN.md`
- Phase 4 = 能力增强（LLM provider 抽象、SQLite、tree-sitter、Web UI）
- Phase 5 = 高阶记忆（衰减 / 再验证 / 记忆图谱 / 多人协作冲突解决）

---

## 6. 执行 AI 自检清单（提交前）

- [ ] 122 + 新增测试 全部通过
- [ ] `debug-mind eval --search-only` hit@1 ≥ 0.92
- [ ] 默认行为零变化（不设新环境变量时 `debug-mind diagnose` 行为同 Phase 1）
- [ ] 旧 `memory/examples/*.md` 仍能 `debug-mind show` / `debug-mind doctor`
- [ ] 新增的顶层依赖只有 `filelock` / `tenacity`（其它都进 optional）
- [ ] 每项任务在执行日志各一行
- [ ] 没改 README 既有章节、Phase 1 schema、CLI 已有命令名

---

## 7. 评审者会检查的具体项

1. **P2-1**：起两个 python 子进程同时 save，sleep 0.1，确认结果完整
2. **P2-2**：mock `response.usage` 让 budget 提前耗尽，看是否 graceful exit + 部分诊断落盘
3. **P2-3**：`DEBUG_MIND_LOG_FORMAT=json debug-mind list` stderr 含合法 JSON
4. **P2-4**：mock client 抛 RateLimitError 两次 → 第三次成功
5. **P2-5**：MCP 设了 token 但 client 不带 → 拒绝写
6. **P2-6**：100 KB error_log → prompt 截断 ≤ 16 KB
7. **P2-7**：人工造孤儿 → `doctor` 报告 → `doctor --fix` 修复
8. **P2-8**：相同 score 下 hit_count 影响排序，eval 不退化

---

## 8. 执行日志

格式：`[YYYY-MM-DDTHH:MM] [TASK P2-X] 简述 + 关键数字 + 取舍`

```
[2026-05-19] [BASELINE] pytest 通过 126 / 失败 1(pre-existing: worktree路径导致rg失败) / 新增0；无 eval 命令（工作单数据与实际不符，以 126 为基线）
[2026-05-19] [TASK P2-1] 并发安全完成：filelock>=3.12，30s timeout，save/delete/verify/mark_used/rebuild_index 均包锁，读不加锁。MemoryBusyError CLI 友好提示。新增 tests/test_concurrency.py（5测试：10进程并发save、锁超时、delete锁、读无锁）。pytest 131 passed / 1 pre-existing fail。取舍：merge master 后 _find_dedup_target+锁共存，dedup 在锁内执行（更安全，略增锁持有时间）。
[2026-05-19] [TASK P2-2] Token/成本预算完成：budget.py 模块（TokenBudget），价格表从 env 可覆盖，agent 每轮 check is_exceeded()，budget exceeded 保存 UNRESOLVED 部分诊断。CLI --max-cost/--max-tokens + env 变量。新增 tests/test_budget.py（11测试）。pytest 136 passed。取舍：budget.py 放在 src/debug_mind/ 而非 src/debug_mind/agent/（后者是文件不是包），不改目录结构。
[2026-05-19] [TASK P2-3] 结构化日志完成：observability/logger.py（JSONFormatter + get_logger），DEBUG_MIND_LOG_FORMAT=json|text，DEBUG_MIND_LOG_FILE=path。trace_id 每诊断生成。MemoryStore save/delete/mark_used/verify 各一条 INFO log。Agent tool call 带 latency_ms、tokens_in/out。OTel hook（_try_otel_span）可选，opentelemetry-api 在 [observability] optional deps。CLI 不设 env 时输出不变。新增 tests/test_structured_logging.py（7测试）。pytest 144 passed。
[2026-05-19] [TASK P2-4] API 重试+兜底完成：tenacity>=8.2，_call_anthropic 用 retry_if_exception(_is_retryable)，只重试 429/5xx/connection，不重试 400/401/403。3 次重试 + 指数退避。API 最终失败时保存 UNRESOLVED 部分诊断。CLI --no-retry flag。新增 tests/test_retry.py（5测试）。pytest 148 passed。
[2026-05-19] [TASK P2-5] MCP 鉴权+审计完成：DEBUG_MIND_MCP_TOKEN env 变量，写工具（save/delete/verify）要求 auth_token 参数匹配，读工具不需要。未设 token 时 WARNING 但保持开放。audit.jsonl 记录所有写操作（CLI + MCP），debug-mind audit CLI 子命令带 --since/--op 过滤。新增 tests/test_mcp_auth.py（10测试）。pytest 158 passed。取舍：auth_token 不能以下划线开头（FastMCP 限制），不实现 RBAC。
[2026-05-19] [TASK P2-6] 输入消毒完成：sanitize.py 模块，description 4KB 截断，error_log 16KB（头8+尾8），env 20 key/256 char，tags 最多 20，控制字符剥离（保留 \n\t\r）。阈值从 env 可调（DEBUG_MIND_MAX_*）。Agent _run_loop 入口 + MCP save 入口各做一遍。新增 tests/test_sanitize.py（15测试）。pytest 172 passed。
[2026-05-19] [TASK P2-7] 一致性 reconciliation 完成：__init__ 清理 >10min 的 .tmp/.pending。verify(correct=False) 改为 .pending→vector delete→.rejected 事务化。doctor() 方法检测 missing/orphan/pending。debug-mind doctor CLI（--fix/--delete-orphans）。新增 tests/test_reconciliation.py（9测试）。pytest 182 passed, 0 failed。
```

---

## 9. 提交建议

每个 P2 任务一个 commit，commit message：

```
feat(concurrency): add filelock around memory writes (Task P2-1)

- 30s timeout, all writes go through FileLock
- read path stays lock-free
- new tests/test_concurrency.py with multi-process fixture
```

不要合并到 master，停在本分支等评审。
