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

每次调试都是从零开始。遇到 Bug，Google 搜索、翻 StackOverflow、翻日志——即使你的同事上周刚解决过完全一样的问题。

**如果调试工具能记住它诊断过的每一个 Bug 呢？**

## 工作原理

```
┌─────────────────────────────────────────────────────────┐
│                     Bug 报告                              │
│           "登录时 NPE，日志中有 Redis 错误"                  │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
              ┌───────────────┐
              │  记忆检索       │  ◄── 向量相似度 (ChromaDB)
              │  "之前见过吗？"  │      + 关键词匹配
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
              ┌───────────────┐
              │  诊断结果 + 修复建议│
              └───────┬───────┘
                      │
                      ▼
              ┌───────────────┐
              │  保存到记忆      │  ──► Markdown 文件（Git 友好）
              │  供下次使用      │  ──► 向量嵌入（可搜索）
              └───────────────┘
```

## 系统架构

DebugMind 有 **四层架构**，每一层都可独立使用：

| 层级 | 组件 | 功能 |
|------|------|------|
| **记忆层** | ChromaDB + Markdown | 混合存储 — 向量搜索 + 人类可读文件 |
| **技能层** | ripgrep / grep | 真实代码搜索、文件读取、项目结构分析 |
| **智能体层** | Claude + ReAct 循环 | 基于工具调用的诊断推理 |
| **协议层** | MCP Server | 将记忆暴露给任何兼容 MCP 的客户端 |
| **交互层** | CLI (Rich) | 带彩色输出的交互式终端 |

### 为什么这样设计？

- **ChromaDB** 是内嵌式的 — 零基础设施，本地即可运行
- **Markdown 文件** 对 Git 友好 — 团队可以共享 Bug 知识库
- **MCP 协议** 让记忆可以被 Claude Code、Claude Desktop 或任何 MCP 客户端访问
- **Agent 循环** 是标准的 ReAct 模式（推理 + 行动），配合工具调用

## 快速开始

```bash
# 安装
pip install -e .

# 如需 OpenAI 兼容提供者（可选）
pip install -e ".[openai]"
DEBUG_MIND_PROVIDER=openai OPENAI_API_KEY=your-key debug-mind diagnose "..."

# 如需自定义嵌入模型（可选）
pip install -e ".[embeddings]"
DEBUG_MIND_EMBEDDING=openai debug-mind rebuild

# 创建 .env 文件，填入你的 API Key
echo "ANTHROPIC_API_KEY=your-key-here" > .env

# 诊断 Bug（结合代码库访问--优势）
debug-mind diagnose --project /path/to/your/codebase \
  --log error.log \
  --env "java=17,framework=Spring Boot 3.2" \
  "高峰期 UserService.login 出现 NullPointerException"

# 或者不依赖代码库诊断（纯记忆模式）
debug-mind diagnose "服务间歇性返回 500"

# 搜索历史案例
debug-mind search "redis connection timeout"

# 浏览记忆库
debug-mind list
debug-mind stats

# 查看或删除特定案例
debug-mind show <case_id>
debug-mind delete <case_id>

# 启动 MCP 服务器（用于 Claude Code / Desktop 集成）
debug-mind serve
```

## MCP 集成

DebugMind 将记忆暴露为 **MCP Server**，任何兼容 MCP 的客户端都可以使用：

```json
// 在你的 MCP 客户端配置中（例如 Claude Desktop 的 claude_desktop_config.json）
{
  "mcpServers": {
    "debug-mind": {
      "command": "python",
      "args": ["-m", "debug_mind.tools.mcp_server"]
    }
  }
}
```

这会给 Claude（或任何 MCP 客户端）提供以下工具：
- `search_similar_bugs` — 搜索历史 Bug 案例
- `save_bug_case` — 保存新诊断到记忆
- `list_recent_bugs` — 浏览最近案例
- `get_bug_stats` — 查看记忆统计
- `delete_bug_case` — 从记忆中删除案例

## 记忆格式

每个 Bug 案例以 Markdown 文件保存在 `memory/cases/` 中：

```markdown
# Redis 连接池耗尽时 UserService.login 的 NPE

> case_id: `abc123` | severity: **high** | status: **fixed**

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

这些文件：
- **可版本控制** — 提交到共享仓库
- **人类可读** — 在任何 Markdown 查看器中浏览
- **可重建** — `debug-mind rebuild` 可将所有文件重新索引到 ChromaDB

## 使用场景

### 个人调试助手
保存你诊断过的每一个 Bug。下次遇到类似问题，DebugMind 几秒内就能找到。

### 团队知识库
通过 Git 共享 `memory/` 目录。每个人的 Bug 诊断都汇聚成共享知识池。

### CI/CD 集成
将构建失败信息输入 DebugMind。如果测试失败报了你之前见过的错误，它会立刻告诉你。

### 面试话题
> "我构建了一个基于 RAG 记忆系统的 AI 调试智能体。它使用向量相似度搜索将新 Bug 与历史诊断匹配，封装为 MCP 服务器，让任何 AI 客户端都能访问知识库。"

## 技术栈

| 组件 | 技术 | 原因 |
|------|------|------|
| LLM | Claude (Anthropic API) | 业界领先的工具调用与推理能力 |
| 智能体框架 | 自定义 ReAct 循环 | 轻量级，无重依赖 |
| 向量数据库 | ChromaDB | 内嵌式，零配置，快速 |
| 持久化 | Markdown 文件 | Git 友好，人类可读 |
| 协议 | MCP (Model Context Protocol) | AI 工具集成标准 |
| CLI | Click + Rich | 美观的终端输出 |
| 数据模型 | Pydantic v2 | 类型安全的数据契约 |

## 项目结构

```
debug-mind/
├── src/debug_mind/
│   ├── schemas.py          # Pydantic 数据模型
│   ├── agent.py            # 核心诊断智能体（ReAct 循环 + 工具调用）
│   ├── cli.py              # CLI 界面（Click + Rich）
│   ├── memory/
│   │   └── store.py        # 混合记忆（ChromaDB + Markdown）
│   ├── skills/
│   │   └── codebase.py     # 真实代码搜索（ripgrep/grep）+ 文件读取
│   └── tools/
│       └── mcp_server.py   # MCP 服务器（供外部客户端使用）
├── memory/
│   └── examples/           # 示例 Bug 案例（Markdown）
├── tests/
│   ├── test_memory_store.py  # 记忆存储 + 代码库技能测试
│   ├── test_agent.py         # 智能体工具分发测试
│   ├── test_cli.py           # CLI 命令测试
│   └── test_schemas.py       # 数据模型校验测试
├── docs/
│   └── logo.svg              # 项目 Logo
└── pyproject.toml
```

## 贡献指南

请参阅 [CONTRIBUTING.md](CONTRIBUTING.md) 了解开发环境配置、代码规范，以及如何添加 Bug 案例或技能。

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 代码检查
ruff check src/ tests/
```

## 路线图

- [ ] 多项目支持（独立的记忆命名空间）
- [ ] Web UI 浏览和搜索知识库
- [ ] 社区 Bug 知识仓库（共享嵌入）
- [ ] 支持 OpenAI / 本地 LLM 模型
- [ ] IDE 插件（VS Code、JetBrains）
- [ ] 基于 NER 的自动标签（从日志中提取框架、语言、模块）

## 许可证

MIT — 随意使用、Fork、二次开发。

---

<p align="center">
  <sub>基于 Claude + ChromaDB + MCP 构建</sub><br/>
  <sub>喂给它的 Bug 越多，它就越聪明。</sub>
</p>
