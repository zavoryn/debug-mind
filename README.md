**中文** | [English](README_EN.md)

<p align="center">
  <img src="docs/logo.svg" alt="DebugMind" width="120" height="120" />
  <h1 align="center">DebugMind</h1>
  <p align="center">
    <strong>基于经验记忆的 AI Bug 诊断智能体</strong><br/>
    <em>诊断过的 Bug 越多，下次定位越快。</em>
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License" />
  <img src="https://img.shields.io/badge/MCP-compatible-purple" alt="MCP Compatible" />
  <img src="https://img.shields.io/badge/RAG-powered-orange" alt="RAG Powered" />
  <a href="https://github.com/zavoryn/debug-mind/actions/workflows/test.yml"><img src="https://github.com/zavoryn/debug-mind/actions/workflows/test.yml/badge.svg?branch=master" alt="tests" /></a>
  <a href="https://github.com/zavoryn/debug-mind/actions/workflows/lint.yml"><img src="https://github.com/zavoryn/debug-mind/actions/workflows/lint.yml/badge.svg?branch=master" alt="lint" /></a>
  <img src="https://img.shields.io/badge/pypi-pre--release-lightgrey" alt="PyPI pre-release" />
</p>

---

## 问题背景

每次调试都是从零开始。遇到 Bug，搜索 Google、翻日志——即使同事上周刚解决过完全一样的问题。

**如果调试工具能记住它诊断过的每一个 Bug 呢？**

## 工作原理

```
┌──────────────────────────────────────────────────────────┐
│                       Bug 报告                            │
│         "登录时 NPE，日志中有 Redis 错误"                  │
└──────────────────────┬───────────────────────────────────┘
                       │
                       ▼
               ┌───────────────┐
               │  记忆检索      │  ◄── 向量相似度 + 关键词匹配
               │  "之前见过吗？" │      (SQLite，无需额外安装)
               └───────┬───────┘
                       │
           ┌───────────┼───────────┐
           ▼                       ▼
    [找到相似案例]             [没有匹配]
           │                       │
    加载历史诊断               全新 AI 诊断
    + 快速定位修复             + 系统化根因分析
           │                       │
           └───────────┬───────────┘
                       ▼
               ┌───────────────────┐
               │  诊断结果 + 修复建议│
               └───────┬───────────┘
                       │
                       ▼
               ┌───────────────────┐
               │  保存到记忆        │  ──► Markdown 文件（Git 友好）
               │  供下次使用        │  ──► 向量嵌入（可搜索）
               └───────────────────┘
```

每次诊断都让下一次更快。已验证的案例搜索排名更高；长期未使用的案例自动衰减，保持知识库的新鲜度。

## 快速开始

```bash
# 安装 — 无需 C 扩展，任何平台都能运行
pip install -e .

# 配置 API Key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# 纯记忆模式诊断（无需代码库）
debug-mind diagnose "服务间歇性返回 500"

# 结合代码库诊断（真正的威力所在）
debug-mind diagnose \
  --project /path/to/your/project \
  --log error.log \
  --env "java=17,framework=Spring Boot 3.2" \
  "高峰期 UserService.login 出现 NullPointerException"

# 搜索历史案例
debug-mind search "redis connection timeout"

# 查看记忆库健康状态
debug-mind doctor
```

## 系统架构

DebugMind 有 **五层架构**，每一层都可独立使用：

| 层级 | 组件 | 功能 |
|------|------|------|
| **记忆层** | SQLite + Markdown | 默认纯 Python，零额外依赖；可选 ChromaDB HNSW 索引（大规模场景） |
| **技能层** | ripgrep / grep | 真实代码搜索、文件读取、项目结构分析 |
| **智能体层** | Claude + ReAct 循环 | 基于工具调用的诊断推理（最多 20 轮，有成本预算） |
| **协议层** | MCP Server | 将记忆暴露给任何兼容 MCP 的客户端 |
| **交互层** | CLI (Rich) + Web UI | 实时流式终端输出，或 Gradio Web 界面 |

### 设计决策

- **SQLite 是默认后端** — 纯 Python stdlib，安装即用，全平台兼容
- **ChromaDB 是可选项** — `pip install debug-mind[chroma]` 用于 1 万+ 案例场景
- **Markdown 是数据源头** — 向量索引随时可以从 Markdown 文件重建
- **MCP 协议** — 让记忆可被 Claude Code、Claude Desktop 或任何 MCP 客户端访问
- **记忆随时间进化** — `verified` 案例提升排名；`hit_count` 对数加权；陈旧案例自动衰减

## 存储后端

| 后端 | 安装 | 适用场景 |
|------|------|---------|
| **SQLite**（默认） | 无需额外安装 | 个人使用，<5K 案例，任何平台 |
| **ChromaDB** | `pip install debug-mind[chroma]` | 团队，大型知识库，HNSW 加速搜索 |

一行环境变量切换后端，Markdown 案例文件全部保留：

```bash
DEBUG_MIND_BACKEND=chroma debug-mind rebuild
```

## 完整命令参考

```bash
# 诊断
debug-mind diagnose "描述" [--project 路径] [--log 文件] [--env k=v,k=v]
                           [--severity critical|high|medium|low]
                           [--max-cost 0.5] [--max-tokens 50000]

# 记忆搜索与浏览
debug-mind search "查询词"     [--top-k 5]
debug-mind list               [--limit 20]
debug-mind show <case_id>
debug-mind stats

# 记忆管理
debug-mind verify <case_id>   --correct | --wrong [--notes "..."]
debug-mind delete <case_id>
debug-mind rebuild             # 从 Markdown 文件重建向量索引
debug-mind doctor              # 检查索引/文件一致性 [--fix]
debug-mind dedupe              # 查找近似重复案例

# 备份与共享
debug-mind export              [--output cases.json] [--limit N]
debug-mind import cases.json   [--skip-existing] [--dry-run]

# 记忆生命周期
debug-mind decay               [--days 30] [--dry-run]
debug-mind reverify            [--days 90]
debug-mind link <A> <B>        [--relation variant|caused_by|fixed_by|related]

# 评测
debug-mind eval                [--search-only] [--case ID] [--json out.json]

# 审计日志
debug-mind audit               [--since 1h|24h|7d] [--op save|verify|delete]

# 集成
debug-mind serve               # 启动 MCP 服务器
debug-mind web                 # 启动 Gradio Web UI [--port 7860]
```

## MCP 集成

将 DebugMind 的记忆连接到 Claude Code 或 Claude Desktop：

```json
{
  "mcpServers": {
    "debug-mind": {
      "command": "python",
      "args": ["-m", "debug_mind.tools.mcp_server"],
      "env": {
        "DEBUG_MIND_MCP_TOKEN": "your-secret-token"
      }
    }
  }
}
```

暴露的 MCP 工具：`search_similar_bugs`、`save_bug_case`、`list_recent_bugs`、`get_bug_stats`、`verify_bug_case`、`delete_bug_case`

## 记忆格式

每个 Bug 案例以 Markdown 文件保存在 `memory/cases/` 中：

```markdown
# UserService.login 在 Redis 连接池耗尽时出现 NPE

> case_id: `abc123` | severity: **high** | status: **fixed** | verified: ✅

## 环境
- language: Java
- framework: Spring Boot 3.2

## 症状
登录返回 500，第 42 行 NullPointerException

## 根因
Redis 连接池耗尽 → getLoginToken() 返回 null → NPE

## 修复建议
1. 将连接池大小增加到 32
2. 在 .equals() 前添加 null 检查

## 标签
npe, redis, spring-boot, connection-pool
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ANTHROPIC_API_KEY` | — | Anthropic API 密钥（必填） |
| `DEBUG_MIND_MEMORY_DIR` | `./memory` | 案例和索引的存储路径 |
| `DEBUG_MIND_BACKEND` | `sqlite` | 存储后端：`sqlite` 或 `chroma` |
| `DEBUG_MIND_PROVIDER` | `anthropic` | LLM 提供者：`anthropic` 或 `openai` |
| `DEBUG_MIND_EMBEDDING` | `default` | 向量化：`default`、`openai`、`voyage`、`bge` |
| `DEBUG_MIND_MAX_COST` | `0.5` | 每次诊断最大花费（USD） |
| `DEBUG_MIND_MAX_TOKENS` | `50000` | 每次诊断最大 token 数 |
| `DEBUG_MIND_MAX_WALL_SECS` | `300` | 诊断挂钟超时（秒） |
| `DEBUG_MIND_LOG_FORMAT` | `text` | 日志格式：`text` 或 `json` |
| `DEBUG_MIND_MCP_TOKEN` | — | MCP 写工具鉴权 token |
| `DEBUG_MIND_MCP_RATE_LIMIT` | `60` | MCP 每分钟最大写请求数 |
| `DEBUG_MIND_AUDIT_MAX_BYTES` | `52428800` | 审计日志轮转大小（50 MiB） |

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| LLM | Claude (Anthropic) | 业界最强工具调用；可切换 OpenAI |
| 智能体循环 | 自定义 ReAct | 最多 20 轮，token/成本预算，挂钟超时 |
| 默认向量存储 | SQLite (stdlib) | 纯 Python，线性余弦搜索，零依赖 |
| 可选向量存储 | ChromaDB | HNSW 索引，推荐 >5K 案例场景 |
| 持久化 | Markdown 文件 | 数据源头，Git 友好 |
| 协议 | MCP | AI 工具集成标准 |
| CLI | Click + Rich | 实时流式输出，逐轮进度显示 |
| Web UI | Gradio | 可选（`pip install debug-mind[web]`） |

## 路线图

- [x] 向量 + 关键词混合检索，verified/hit_count 排序
- [x] 可插拔向量化提供者（OpenAI、Voyage、BGE、默认）
- [x] MCP 服务器（鉴权 + 限流 + 审计日志）
- [x] Token/成本预算与挂钟超时
- [x] 并发写安全（filelock）
- [x] ChromaDB 和 SQLite 双后端（可切换）
- [x] Gradio Web UI
- [x] OpenAI 提供者支持
- [x] 记忆生命周期：衰减、再验证、案例关联
- [x] CI/CD 工作流 + 198 个测试
- [x] Export/Import 跨机器记忆共享
- [ ] PyPI 正式发布（`pip install debug-mind`）
- [ ] 多项目记忆命名空间
- [ ] IDE 插件（VS Code、JetBrains）
- [ ] 社区基准案例扩展（100+ 案例）

## 贡献指南

请参阅 [CONTRIBUTING.md](CONTRIBUTING.md) 了解开发环境配置、代码规范以及如何添加基准案例或新技能。

```bash
pip install -e ".[dev]"
pytest                         # 198 个测试
ruff check src/ tests/         # 代码检查
debug-mind eval --search-only  # 检索质量评测
```

## 许可证

MIT — 随意使用、Fork、二次开发。

---

<p align="center">
  <sub>基于 Claude · SQLite · MCP 构建</sub><br/>
  <sub>喂给它的 Bug 越多，它就越聪明。</sub>
</p>
