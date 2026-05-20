# DebugMind Phase 4 — 能力增强

> 这是 **Phase 4 工作单**。Phase 1-3 已完成；本阶段扩展模型支持、存储后端、代码理解与可扩展性。
> 写作风格沿用 REFACTOR_PLAN：契约严格，内部宽松；按编号顺序做。

---

## 0. 工作纪律

1. **从基线开始**。先 `pytest -v` 确认全绿、`debug-mind eval --search-only` 跑通。
2. **一次一个任务**。做完跑测试 → 跑 eval → commit → push → 再开下一个。
3. **不要跳到 Phase 5**。
4. **不要破坏既有 API**。CLI 命令名、MCP 工具名、BugCase schema 不变。
5. **不要引入重依赖**。tree-sitter 按语言分包，用户选装。

---

## 1. 背景与目标

Phase 2/3 之后项目在工程化上已基本完备，但能力侧有三块明显短板：

- 只能调 Anthropic（用户想用 OpenAI / 本地模型）
- 只能 ChromaDB（SQLite 对单人部署更友好）
- 代码理解只有 ripgrep（没有 AST 级理解）
- 没有 UI（路人没法试）
- 没有插件机制（贡献者没法写扩展）

Phase 4 解决这五件事。

---

## 2. 全局约束

| 约束 | 说明 |
|------|------|
| 已有测试 + eval 必须不退化 | 任何改动回跑 eval |
| 不破坏 CLI / MCP / schema | Phase 1-3 的接口不动 |
| Anthropic 仍是默认 | 新 provider 通过 env/config 切换 |
| ChromaDB 仍是默认 | SQLite 通过 env 切换 |
| 新增依赖进 optional | 不增加默认安装的重量 |

---

## 3. 任务

### P4-1 — LLM Provider 抽象

**Why**：现在 `agent.py` 硬编码 `anthropic.Anthropic`，换模型要改源码。

**改什么**
1. 新模块 `src/debug_mind/providers/`：`base.py`（协议）、`anthropic_provider.py`、`openai_provider.py`
2. `DiagnosticAgent.__init__` 接受 `provider: LLMProvider = None`，默认 Anthropic
3. OpenAI provider 用 `openai` 包，Anthropic tool schema 转 OpenAI function calling 格式
4. 环境变量 `DEBUG_MIND_PROVIDER=openai` + `OPENAI_API_KEY` 切换

**验收**
- 单测：mock Anthropic client + OpenAI client 都能跑通 diagnose
- eval 不变

### P4-2 — SQLite 存储后端

**Why**：ChromaDB 依赖 onnxruntime（1.2GB），对单人部署太重。

**改什么**
1. `src/debug_mind/memory/backends/`：`base.py`（协议）、`chroma_backend.py`、`sqlite_backend.py`
2. SQLite 用 `sqlite-vec` 扩展做向量搜索（纯 SQL，无额外进程）
3. 环境变量 `DEBUG_MIND_BACKEND=sqlite` 切换
4. 搜索接口不变，MemoryStore 内部多态

**验收**
- 单测：两个后端跑同一套 MemoryStore 测试
- `debug-mind eval --search-only` 分数不退化

### P4-3 — tree-sitter 代码解析

**Why**：现在 `search_code` 是 regex/ripgrep，不知道函数边界、AST 结构。

**改什么**
1. `src/debug_mind/skills/parser.py`：tree-sitter 封装
2. 支持 Python / JavaScript / TypeScript / Java / Go 五种语言（按需安装语法包）
3. 新工具 `parse_symbol(code, symbol)`：返回函数/类的定义位置和文档
4. 集成到 `list_project_structure`：给出结构化的符号表

**验收**
- 单测：对已知代码解析出正确的符号列表
- 安装 tree-sitter-python 后即可用，不装则工具返回 "parser not available"

### P4-4 — Web UI（Gradio）

**Why**：CLI 门槛高，路人 3 秒内没法试。

**改什么**
1. `src/debug_mind/web.py`：Gradio 界面
2. 三个 tab：Diagnose（输 bug + 看结果）、Memory（搜索/浏览历史）、Stats
3. `debug-mind web` 命令启动
4. gradio 进 `[project.optional-dependencies].web`

**验收**
- `debug-mind web` 启动后浏览器可访问
- 输入 bug 描述 → 看到诊断结果

### P4-5 — 插件机制

**Why**：当前加新工具/技能要改源码，第三方没法贡献。

**改什么**
1. `src/debug_mind/plugins.py`：插件发现与加载
2. 插件是一个 Python 包，包含 `setup(app)` 函数
3. setup 可注册：自定义工具、自定义 embedding、自定义 reranker
4. `DEBUG_MIND_PLUGINS=path/to/plugin1,path/to/plugin2` 加载
5. `debug-mind plugins` 列出已加载插件

**验收**
- 单测：写一个 mock plugin 注册自定义 tool，diagnose 循环中可见

---

## 4. Phase 5 占位（本轮不做）

- 记忆衰减 / 再验证 / 记忆图谱 / 多人协作冲突解决

---

## 5. 执行日志

```
[填] [BASELINE] pytest X 通过；eval search-only hit@1=0.22 (50-case)
```

## 6. 提交建议

每个 P4 任务一个 commit：
```
feat(provider): add LLM provider abstraction with OpenAI support (Task P4-1)
```
