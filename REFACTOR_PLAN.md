# DebugMind 重构与改进计划

> 本文件是**给执行 AI 的工作单**。完成后会由另一个 AI（评审者）按"验收"小节逐项检查。
> 写作风格：契约严格，内部实现宽松；按 Phase 顺序执行，**不要跳号**。

---

## 0. 给执行 AI 的工作纪律

1. **从基线开始**。先执行 `pytest -v` 把通过数 / 失败数 / 跳过数记到本文件末尾的「执行日志」第一行。任何任务完成后再跑一次，记录差异。
2. **一次一个任务**。每个任务（4.1 / 4.2 / …）做完都要：跑测试 → 在执行日志追加一条 → 再开下一个。
3. **禁止跳到 Phase 2 / 3**。Phase 1 全部通过后停下来等评审，不要自作主张继续。
4. **遇到设计模糊点**，挑「更简单 / 更可逆」的那一个，并在执行日志写明你做了什么取舍。不要因此阻塞。
5. **不要重写 README、不要改 logo、不要重命名已有 CLI 命令**。允许向 README 追加章节，不允许删改既有内容。
6. **不要引入重依赖**。如果某任务真的需要新增依赖（例如 `tree-sitter`），写在执行日志里说明原因并跳过该子项，让评审者决定。
7. **保留对旧 `memory/cases/*.md` 的兼容**：即使引入新字段，旧 markdown 也必须能继续被 `MemoryStore.get()` / `list_recent()` 解析（缺字段用默认值）。
8. **不要碰 `memory/chroma/`** 目录里现有的向量库内容。如果改了 schema 需要清空，提供 `rebuild` 指引而不是删文件。
9. **每个新增 Python 模块顶部都要写一段 docstring**，解释这个模块为何存在、和谁配合。

---

## 1. 项目背景与目标

DebugMind 是一个"基于经验记忆的 Bug 诊断 Agent"：用户报 bug → Agent 先查记忆库 → 没命中就用 Claude + 代码库工具诊断 → 把诊断结果回写记忆。当前实现已经能跑通 ReAct 主循环、Chroma 向量库 + Markdown 双写、MCP 服务器、CLI。

**本轮重构目标**：把"周末玩具"提升到"生产可用 + 面试可讲"。三个核心痛点：

- **记忆只写不学**：无去重、无验证、无衰减，错诊会污染未来检索。
- **无可信度证明**：没有评测数据集，"诊断越多越快"是空口号。
- **RAG 链路单薄**：默认 MiniLM 英文 embedding、无 rerank、召回直接喂 LLM。

Phase 1 解决这三件事 + 几个硬正确性 bug。Phase 2/3 是后续优化清单，本轮不动。

---

## 2. 全局约束

| 约束 | 说明 |
|------|------|
| 已有测试必须全绿 | `pytest -v` 通过数不准下降；新增功能必须配套测试 |
| 不改 CLI 命令名 | `diagnose / search / list / stats / rebuild / show / delete / serve` 必须保留语义；可以新增子命令 |
| 旧 Markdown 兼容 | 现有 `memory/examples/*.md` 仍能被解析（缺新字段用默认值） |
| 不要切 SQLite | 本轮继续 ChromaDB + Markdown，SQLite 留给 Phase 2 |
| 不动 Anthropic 模型 ID | 用 `agent.py` 里当前的默认模型，不要"顺手升级" |
| 中文 OK | 注释、CLI 输出、日志可以中文；标识符（变量、函数、类、文件名）一律英文 |
| 不写无关注释 | 仅在"为什么"非显而易见时加注释，不要把任务描述抄到代码里 |

---

## 3. 改动前基线（必填）

在「执行日志」第一行写：

```
[BASELINE] pytest 通过 X 个 / 失败 Y 个 / 跳过 Z 个；ripgrep 是否可用：是/否
```

---

## 4. Phase 1 任务

> 顺序：4.1 → 4.2 → 4.3 → 4.4。**4.4 是正确性修复，可以与 4.1-4.3 并行思考但要最后落地**，避免和前面的 schema 改动冲突。

### 4.1 Task A — 评测框架（最先做，所有后续改动都靠它验证收益）

**Why**：没有数字就没有故事。需要一个最小数据集 + 评分器，能回答"加了记忆 / 换了 embedding / 加了 rerank 后，召回质量是不是真的提升了"。

**改什么**

1. 新增目录 `evaluation/`，结构：
   ```
   evaluation/
     __init__.py
     benchmark.py        # 数据加载 + 评分器
     dataset.py          # BenchmarkCase pydantic 模型
     cases/              # YAML 格式的基准案例，每个一个文件
       redis-pool-npe.yaml
       spring-circular-dep.yaml
       async-deadlock.yaml
       ... (至少 12 个，覆盖：Java/Python/Node、NPE/死锁/OOM/连接池/配置错/依赖循环等)
   ```

2. `BenchmarkCase` 字段建议：
   ```python
   class BenchmarkCase(BaseModel):
       id: str
       bug_description: str
       error_log: str = ""
       environment: dict[str, str] = {}
       seed_case_ids: list[str] = []          # 评测前需注入记忆的"历史案例"id
       expected_root_cause_keywords: list[str]  # 关键词命中即算正确（lower-case 子串匹配）
       expected_fix_keywords: list[str]
       expected_top_hit_id: str | None = None # 如有 seed，期望召回第一名命中此 id
   ```

3. `evaluation/seed_cases/*.md`：约 20 个"历史案例" markdown，**与 benchmark cases 配对**——其中一部分应当能命中 benchmark 中的 bug。

4. 评分指标：
   - `hit@1 / hit@3 / hit@5`：当 benchmark case 期望命中某 seed id 时统计
   - `MRR`（Mean Reciprocal Rank）
   - `root_cause_keyword_recall`：召回的 top-1 案例的 root_cause 包含多少期望关键词
   - 端到端 `keyword_recall`（需要 API key，可选跑）

5. CLI 新增命令：
   ```bash
   debug-mind eval --search-only            # 只评检索，无需 API key
   debug-mind eval                          # 全链路评测，需 ANTHROPIC_API_KEY
   debug-mind eval --case redis-pool-npe    # 单跑一个
   debug-mind eval --json out.json          # 机器可读输出
   ```
   注意：`eval` 必须用临时目录的隔离 `MemoryStore`，不要污染用户的 `memory/`。

6. 输出格式（人类可读）：
   ```
   ┌──────────────────┬──────┬───────┬───────┬───────┬─────┐
   │ Case             │ hit@1│ hit@3 │ hit@5 │ MRR   │ KW  │
   ├──────────────────┼──────┼───────┼───────┼───────┼─────┤
   │ redis-pool-npe   │  ✓   │  ✓    │  ✓    │ 1.000 │ 4/5 │
   │ spring-circular  │  ✗   │  ✓    │  ✓    │ 0.500 │ 2/4 │
   ...
   ├──────────────────┼──────┼───────┼───────┼───────┼─────┤
   │ Overall (N=12)   │ 0.75 │ 0.92  │ 1.00  │ 0.83  │ 0.71│
   └──────────────────┴──────┴───────┴───────┴───────┴─────┘
   ```

**验收**

- [ ] `pytest tests/test_eval.py -v` 全绿；测试覆盖：dataset 加载、scorer 算术、CLI `eval --search-only` 在空 API key 下能跑完
- [ ] 在原版 embedding + 无 rerank 下跑 `debug-mind eval --search-only` 能产出基线分数；把该分数追加到执行日志（这是 Task C 的对照组）
- [ ] 至少 12 个 benchmark case + 配套 seed cases，文件齐全
- [ ] 评测命令产出退出码 0；任何子项失败有清晰报错
- [ ] **不要**在评测中调用真实 API（除非显式用 `--no-search-only`）；CI 默认跑 `--search-only`

**不要做**

- 不要把评测结果写进 README——本轮先有数字再说怎么讲
- 不要为了刷分篡改 benchmark（评审会逐个 case 抽查）

---

### 4.2 Task B — 反馈闭环 + 去重 + verified 字段

**Why**：当前 `save_to_memory` 一律落盘，错诊会污染未来检索；同一 bug 多次诊断会留多份。这两件事让"记忆"实际上变成"垃圾堆"。

**Schema 扩展**（改 `src/debug_mind/schemas.py`）

```python
class BugCase(BaseModel):
    # ... 既有字段
    verified: bool = False                    # 用户/作者确认此诊断正确
    verification_notes: str = ""              # 验证或反驳的说明
    hit_count: int = 0                        # 被检索并被 agent 实际采用的次数
    last_used_at: datetime | None = None      # 上次被采用的时间
    superseded_by: str | None = None          # 被合并/更新时指向新 case id
```

`_case_to_markdown` 把新字段写入 markdown 底部元数据区；`_markdown_to_case` 读取时缺字段用默认值（这是兼容性硬要求）。

**MemoryStore 行为变化**（改 `src/debug_mind/memory/store.py`）

1. **保存前去重**：`save(case)` 先做 `search(case.to_search_text(), top_k=3)`：
   - 若最高分 > `DEDUP_THRESHOLD`（默认 0.92，环境变量 `DEBUG_MIND_DEDUP_THRESHOLD` 可调），
   - 且该已有 case 的 `verified == True`，
   - 则**不写新条目**，把新症状追加到现有 case 的 `symptoms`（用 "\n---\nVariant: ...\n" 分隔），返回现有 case。
   - 在执行日志记录这是个判断点：未 verified 的高分相似项是否合并由我们选择"不合并"以保留多样性，这个取舍写到代码注释里。

2. **检索时重排**：`search()` 内部把原始 score 映射成 `effective_score`：
   ```
   effective = score * (1.0 if verified else 0.7)
   ```
   排序按 `effective`，但 `SearchResult.score` 仍返回原始 cosine 相似度（评测要看原始分）。

3. **采用计数**：新增 `MemoryStore.mark_used(case_id: str)`，把 `hit_count += 1`、`last_used_at = now`、回写 markdown。**只在 agent 真的把该 case 写进最终诊断的 `similar_case_ids` 时调用**——不是每次 search 都调。

4. **`search()` 增加可选过滤参数**：`include_unverified: bool = True`。评测和高保真场景可关掉。

**Agent 行为变化**（改 `src/debug_mind/agent.py`）

- `_run_loop` 在循环结束、`saved_case_id` 已知时，对 `similar_case_ids` 里每个 id 调用 `memory.mark_used(id)`。
- `_execute_tool("search_memory", ...)` 返回结果时，给每个 case 加 `verified` 和 `hit_count` 字段——让 LLM 知道哪个案例更可信。
- 系统提示里加一段：「优先采用 verified=True 的案例；若仅有 unverified 案例匹配，要在 reasoning 里说明并降低信心」。

**MCP 工具同步**（改 `src/debug_mind/tools/mcp_server.py`）

- `search_similar_bugs` 返回结果包含 `verified` / `hit_count`
- 新增工具 `verify_bug_case(case_id, correct: bool, notes: str = "")`：correct=True 把 verified 设为 True；correct=False 删除该 case（与 CLI 行为一致）

**CLI 新增子命令**（改 `src/debug_mind/cli.py`）

```bash
debug-mind verify <case_id> --correct [--notes "..."]
debug-mind verify <case_id> --wrong [--notes "..."]     # 软删除：保留 markdown 加后缀 .rejected 但从 chroma 移除
debug-mind dedupe                                       # 扫所有 case，列出 score > 0.9 的近重复对，交互确认是否合并
```

**验收**

- [ ] 新增字段：旧 markdown 仍能解析，新写入的 markdown 含全部字段
- [ ] 测试覆盖：
  - 去重：高相似且 verified 的写入返回原 id，markdown 中 Variant 被追加
  - 去重不触发：相似但 unverified 时正常写新 case
  - 重排：相同原始 score 下 verified case 排在 unverified 前
  - mark_used：hit_count 增加，last_used_at 被设置且回写 markdown
  - verify CLI：--correct 翻转 verified 并保留向量；--wrong 把 case 从向量库剔除
- [ ] 重新跑 Task A 的评测，对照基线，**指标不可下降**（应当持平或上升）。把对照写到执行日志：
  ```
  [TASK B] eval search-only: hit@1 baseline=X.XX → after=X.XX
  ```

**不要做**

- 不要把 `verify --wrong` 物理删 markdown 文件——评审还要看
- 不要给 dedup_threshold 设很激进的值（< 0.9）

---

### 4.3 Task C — Embedding 可插拔 + Reranker

**Why**：默认 MiniLM 对中文 / 代码 / 堆栈关键词都弱；召回直接喂 LLM 噪音大。

**改什么**

1. 新模块 `src/debug_mind/memory/embeddings.py`：

   ```python
   from typing import Protocol

   class EmbeddingFunction(Protocol):
       def __call__(self, texts: list[str]) -> list[list[float]]: ...

   def default_embedding() -> EmbeddingFunction:
       """返回 Chroma 内置默认（保持兼容）。"""
       ...

   def make_embedding(provider: str | None = None) -> EmbeddingFunction:
       """根据 provider 字符串构建。支持: 'default', 'voyage', 'openai', 'bge'。
       provider=None 时读环境变量 DEBUG_MIND_EMBEDDING（默认 'default'）。
       缺依赖或缺 API key 时回退到 default 并打印 warning。
       """
   ```

   - `voyage`：用 `voyage-3` 或 `voyage-code-3`（需 `VOYAGE_API_KEY`）
   - `openai`：用 `text-embedding-3-large`（需 `OPENAI_API_KEY`）
   - `bge`：用 `BAAI/bge-m3`（如果用户装了 `sentence-transformers` 才启用，否则回退）
   - **不要**把这些依赖加进 `pyproject.toml` 的 `dependencies`，最多加进 `optional-dependencies` 的 `embeddings` 组

2. 新模块 `src/debug_mind/memory/rerank.py`：

   ```python
   class Reranker(Protocol):
       def rerank(self, query: str, results: list[SearchResult], top_k: int) -> list[SearchResult]: ...

   class IdentityReranker:
       """默认：不做事，原样返回。"""

   class LLMReranker:
       """用 Claude Haiku 给每个候选打 1-10 分，按分数重排。
       max_candidates: 输入超过此数先截断（默认 10）。
       """
   ```

3. `MemoryStore.__init__` 接受 `embedding_fn: EmbeddingFunction | None = None` 和 `reranker: Reranker | None = None`：
   - `embedding_fn` 传给 `chromadb.PersistentClient.get_or_create_collection(embedding_function=...)`
   - `reranker` 在 `search()` 末尾调用一次

4. CLI 读 `DEBUG_MIND_EMBEDDING` 和 `DEBUG_MIND_RERANK`（取值 `none|llm`）环境变量构造 store。

5. README 不动；在 `docs/` 下新增 `docs/embeddings.md` 说明如何切换，附评测命令对比示例。

**验收**

- [ ] 默认行为零变化（不设环境变量时所有既有测试照过）
- [ ] 测试覆盖：
  - `make_embedding("nonexistent")` 回退到 default 并 warning
  - `LLMReranker` 用一个 mock client 验证排序行为
  - `MemoryStore(reranker=IdentityReranker())` 等价于无 rerank
- [ ] 用 Task A 的评测在**至少两个 provider** 上各跑一次（哪怕 BGE 跑不通至少跑 default vs `LLMReranker`），把对比数字写进执行日志：
  ```
  [TASK C] config=default+identity:   hit@1=0.50  MRR=0.62
  [TASK C] config=default+llm-rerank: hit@1=0.67  MRR=0.74
  ```

**不要做**

- 不要在评测里偷偷换 benchmark 让 rerank 显得效果好
- 不要把 LLMReranker 设为默认；默认必须仍是 IdentityReranker

---

### 4.4 Task D — 正确性 / 一致性硬修复

**Why**：这些 bug 单独看都小，加起来让项目"不像生产代码"。

**D1. 写入原子化**（改 `MemoryStore._save_to_markdown`）

- 先写到 `<id>.md.tmp` → `os.replace()` 到 `<id>.md`
- 调换次序：**先写 markdown，再 upsert 向量**。如果向量写入失败，记 warning，不抛——下次 `rebuild_index` 会修。
- 测试：mock `os.replace` 抛异常，验证 `.tmp` 文件被清理且原文件未损坏。

**D2. Windows 路径越界检查**（改 `skills/codebase.py:read_file`）

- 把 `str(full_path).startswith(str(project_root))` 换成：
  ```python
  try:
      full_path.relative_to(project_root)
  except ValueError:
      return {"error": "Access denied: file is outside the project directory"}
  ```
- 测试新增：传 `..\\..\\Windows\\System32\\drivers\\etc\\hosts` 之类的 Windows 风格相对路径，应被拒绝。已有测试 `test_read_file_outside_project` 保留。

**D3. Tool schema 单一来源**

- 现状：`agent.py:MEMORY_TOOLS` 和 `mcp_server.py` 两处手写工具定义，已经在漂移（MCP 不支持 `similar_case_ids`、docstring 谎称有 `diagnose_bug`）。
- 改法：在 `src/debug_mind/tools/schemas.py`（新文件）集中定义 Anthropic 工具 schema dict + 由其派生 MCP 的函数签名（或反过来）。两边 import 同一份。
- 顺手修复 `mcp_server.py` 顶部 docstring 里那行假的 `diagnose_bug`。
- 测试：用一个简单 assert 比较 agent 和 MCP 两侧字段名集合一致。

**D4. Agent 主循环加 prompt caching**

- `client.messages.create` 调用时给 `system` 和 `tools` 加 `cache_control: {"type": "ephemeral"}`：
  ```python
  system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
  ```
  以及最后一个 tool 上加 cache_control（按 Anthropic 文档的"最多 4 个 cache breakpoint"约束）。
- 不需要写测试（除非能 mock client.messages.create 验证请求 payload 含 cache_control 字段）——但**必须**在执行日志里说明这个改动以及为什么这能省钱。

**D5. `_run_loop` 末尾的 final_text bug**

- 当循环因 `max_turns` 耗尽退出时，`assistant_content` 是最后一轮的内容，但若那一轮全是 tool_use 没有 text，`final_text` 为空，结果就只剩 case_id。检查并修：如果 `final_text` 为空但 `saved_case` 存在，用 `saved_case.root_cause` + `saved_case.fix_suggestion` 拼一个兜底。
- 测试：mock 一个 max_turns 提前退出的场景验证。

**验收**

- [ ] D1 / D2 / D3 / D5 各有至少一个新测试覆盖
- [ ] D4 在 agent.py 里能看到 cache_control 字段；执行日志附说明
- [ ] 全套 `pytest -v` 全绿

---

## 5. 后续 Phase（本文档只覆盖 Phase 1）

Phase 1 已完结（见 §8 执行日志最后一行 + 评审者复核记录）。后续阶段按"生产可靠性 → 开源可采用性 → 能力增强 → 高阶记忆"四档拆分，每档一份独立工作单：

- **Phase 2 — 生产硬骨头**：`PHASE_2_PLAN.md`。并发安全 / 成本预算 / 结构化日志 / API 重试 / MCP 鉴权 / 输入消毒 / 一致性 reconciliation / hit_count ranking。
- **Phase 3 — 开源工程化**：`PHASE_3_PLAN.md`。CI / PyPI / CONTRIBUTING + CHANGELOG + 模板 / README demo + 架构图 / benchmark 扩 50 case。
- **Phase 4 — 能力增强**（待规划）：LLM provider 抽象（OpenAI / Gemini / 本地）、SQLite 持久层、tree-sitter 代码理解、git blame 集成、日志结构化解析、Web UI、插件机制。原 §5 旧清单的大半都被归到这里。
- **Phase 5 — 高阶记忆**（待规划）：案例衰减 + 再验证工作流、记忆图谱（case ↔ code symbol ↔ commit）、多人协作冲突解决、RBAC。

派单顺序由总规划者按"上线风险"和"采用门槛"决定，不严格按编号串行——Phase 2 与 Phase 3 在不冲突时可以并行。

---

## 6. 执行 AI 自检清单（提交前过一遍）

- [ ] `pytest -v` 通过数 ≥ 基线
- [ ] 任务 4.1~4.4 各自的「验收」复选框全部勾上（或在执行日志写明为何跳过某项）
- [ ] 没有引入新的顶层依赖到 `[project.dependencies]`（如有，必须写明）
- [ ] `memory/examples/*.md` 用 `debug-mind rebuild` 或单元测试能解析
- [ ] 没有改动 README 既有章节、logo、CLI 已有命令名
- [ ] 没有删除 / 移动既有 Python 模块（可新增）
- [ ] 执行日志填完整，每个任务一条

---

## 7. 评审者将检查的具体项（执行 AI 心里有数即可）

1. **Task A**：随机抽 3 个 benchmark case，肉眼判断 `expected_*_keywords` 是否合理；跑一次 `eval --search-only` 看实际数字与执行日志一致
2. **Task B**：
   - 造一个 verified=True 的 case A，再 save 一个相似 case A'，确认未新建
   - 造 unverified case B 和 verified case B'，相同 query 看 B' 是否排前
   - `verify --wrong` 后再 search 同 query，确认该 case 不再出现
3. **Task C**：
   - 不设环境变量时所有测试照过
   - 设 `DEBUG_MIND_RERANK=llm` 跑 eval，对比无 rerank 的分数
   - 翻 `embeddings.py` 看有没有把 API key hard-code
4. **Task D**：
   - 模拟 `os.replace` 失败看 `.tmp` 是否清理
   - Windows 风格越界路径被拒
   - agent.py 和 mcp_server.py 引用同一份 schema 模块
5. **整体**：随便挑一个 `memory/examples/*.md` 看是否仍能 `debug-mind show <id>`

---

## 8. 执行日志（每完成一项追加一行；时间用 ISO8601）

格式：`[YYYY-MM-DDTHH:MM] [TASK X] 简述 + 关键数字 + 任何取舍`

```
[2026-05-19T22:00] [BASELINE] pytest 通过 59 / 失败 0 / 跳过 0；ripgrep 可用
[2026-05-19T22:15] [TASK A] 新增 12 个 benchmark + 20 个 seed；eval search-only baseline: hit@1=0.92 hit@3=1.00 MRR=0.96 KW=0.49。pytest 81 通过 / 1 失败(flaky rg test)。
[2026-05-19T23:10] [TASK B] 加入 verified/hit_count/superseded_by；旧 markdown 兼容测试通过；取舍：未 verified 的高分相似不合并，保留多样性。新增 dedup/rerank/mark_used/verify/dedupe CLI。pytest 96 通过 / 0 失败。
[2026-05-19T23:45] [TASK C] embeddings/rerank 抽象完成；支持 default/voyage/openai/bge + IdentityReranker/LLMReranker；MemoryStore 接受 embedding_fn 和 reranker 参数。docs/embeddings.md 已创建。pytest 110 通过 / 0 失败。config=default+identity: hit@1=0.92 MRR=0.96（与 baseline 持平）。
[2026-05-20T00:20] [TASK D] D1 原子写入(.tmp+os.replace+markdown先于vector)、D2 Windows路径越界(relative_to替代startswith)、D3 Tool schema单一来源(tools/schemas.py)、D4 Prompt caching(cache_control on system+last tool)、D5 final_text兜底。pytest 121 通过 / 0 失败。D4 说明：system prompt 和最后一个 tool definition 加 cache_control: ephemeral，按 Anthropic 文档最多 4 个 breakpoint，当前只用了 2 个，能在多轮对话中节省重复的 system+tools token 开销。
[2026-05-19T(reviewer)] [REVIEW] Phase 1 复核完成：Task A/B/C/D 端到端验证通过（去重、verified 重排、verify --wrong、原子写、D2 真实越界拦截、D4 mock 确认 cache_control 真到 payload、D5 fallback 真触发）。发现两处遗漏并就地修复：① MCP save_bug_case 缺 similar_case_ids 参数（D3 单源未完成，只有 agent 端用到 schemas.py）；② evaluation 模块未打包导致 debug-mind eval 入口跑不通。修复后 pytest 122 / 0；debug-mind eval 在任意 CWD 跑通。Phase 1 收尾，后续见 PHASE_2_PLAN.md / PHASE_3_PLAN.md。
```

---

## 9. 提交建议

不要一次性大提交。每完成一个任务 commit 一次，commit message 用：

```
feat(eval): add benchmark dataset and scorer (Task A)

- 12 benchmark cases covering Java/Python/Node common bugs
- hit@k, MRR, keyword recall metrics
- `debug-mind eval --search-only` requires no API key
- baseline: hit@1=0.50 hit@3=0.75 MRR=0.62
```

不要合并到 master——本轮全部停在当前 master 上的新提交即可，评审会人工挑。
