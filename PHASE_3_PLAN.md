# DebugMind Phase 3 — 开源工程化

> 这是 **Phase 3 工作单**。Phase 1（REFACTOR_PLAN）和 Phase 2（PHASE_2_PLAN）的代码改动到位后，本阶段把项目从"自己能跑"提升到"陌生人愿意 PR"。
> 全部是工程基建 + 文档 + benchmark 扩展，**不动业务代码**。

---

## 0. 给执行 AI 的工作纪律

1. **不要改业务代码**。Phase 1 / 2 的成果（`src/debug_mind/`、`evaluation/`、`tests/`）只允许追加测试与文档注释，不允许改逻辑。
2. **不要重写已有 README**。允许在 README.md / README_EN.md 头部追加 demo 区块、追加 "Why DebugMind" 段落，不允许删既有内容。
3. **不要引入额外运行时依赖**。CI / 文档需要的只是 GitHub Actions YAML + markdown 文件，不该改 `[project.dependencies]`。
4. **不要把 secrets / API key 写进 CI**。所有 secret 通过 `${{ secrets.X }}` 引用，README 写明用户怎么配置。
5. **不要发真包到 PyPI**。本阶段只搭好流水线，第一次发布前必须人工确认（手工 dispatch workflow 才发，不要 push tag 就自动发）。
6. **每个新增 markdown 文档顶部写一段"读这份文档的人是谁"**。
7. **遇到设计模糊点**，挑业内默认习惯（GitHub Actions 官方推荐 / Keep a Changelog / Conventional Commits），写到执行日志里。

---

## 1. 背景与目标

DebugMind 在 Phase 1+2 之后代码质量过得去，但从 GitHub 路人到"装包用上 + 提 PR"这条链路 100% 是空的：

- 没 CI：PR 提了没人帮跑测试
- 没 PyPI：装包只能 `pip install -e .`，不友好
- 没 CONTRIBUTING / CHANGELOG / 模板：贡献者第一次想 PR 都不知道分支策略
- README 没 demo：路人 3 秒内不知道这东西能干啥
- 12 个 benchmark case 是 sanity check 不是 benchmark：故事讲不响

Phase 3 把这 5 件事做了。**不做** Web UI（Phase 4）、不做多语言文档翻译（按需）、不做赞助 / 商标合规等社区治理工作（也按需）。

---

## 2. 全局约束

| 约束 | 说明 |
|------|------|
| 122 + Phase 2 新增测试 必须仍全绿 | Phase 3 不该破坏任何测试 |
| 不改 CLI / API / schema | 这是文档 / 工程化阶段，行为零变化 |
| README 既有章节保留 | 只允许追加；中文 README 仍为默认 |
| Python 矩阵：3.10 / 3.11 / 3.12 / 3.13 | CI 必须覆盖全部 |
| Windows + Linux 都跑 | CI 矩阵必须有 windows-latest 和 ubuntu-latest |
| 标识符英文，注释 / 文档可中英双语 | 文档英文优先（开源面向全球），关键章节附中文翻译可选 |

---

## 3. 改动前基线

执行日志第一行：

```
[BASELINE] pytest 通过 X / 失败 0；eval search-only hit@1=0.92；当前 README.md 行数 Y；尚无 .github/ 目录
```

---

## 4. Phase 3 任务

> 顺序：3.1 → 3.2 → 3.3 → 3.4 → 3.5。**3.5（benchmark 扩 50 case）最耗时也最有体力活，留到最后**。

### 4.1 Task P3-1 — CI 流水线

**Why**：PR 提了没自动检查 = 维护者要一个个跑测试 = 不会有维护者。

**改什么**

1. 新建 `.github/workflows/test.yml`：
   ```yaml
   name: tests
   on:
     push: { branches: [master] }
     pull_request:
   jobs:
     test:
       strategy:
         matrix:
           os: [ubuntu-latest, windows-latest]
           python: ["3.10", "3.11", "3.12", "3.13"]
       runs-on: ${{ matrix.os }}
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with:
             python-version: ${{ matrix.python }}
             cache: pip
         - run: pip install -e .[dev]
         - run: ruff check src tests
         - run: pytest -v --tb=short
         - run: debug-mind eval --search-only
   ```
2. 新建 `.github/workflows/lint.yml`：仅跑 `ruff format --check` + `ruff check`，3.12 单矩阵。
3. README 顶部加 badge：tests / lint / pypi version（PyPI 那个先占位 `pre-release`）。
4. **Windows 矩阵特殊处理**：ripgrep 未必默认装，CI 步骤需 `choco install ripgrep` 或允许跳过 ripgrep 相关测试（参考已有的 skip 逻辑）。
5. 跑时间预算：单个 job ≤ 5 分钟。如果超过，先 cache pip 再看。

**验收**

- [ ] PR 推上来后 8 个 job（2 OS × 4 Python）全绿
- [ ] 故意推一个 ruff 报错的 commit 验证 lint job 红
- [ ] 故意推一个 pytest 失败的 commit 验证 test job 红
- [ ] README badge 显示正确

**不要做**

- 不要把 `ANTHROPIC_API_KEY` 加进 CI（`eval --search-only` 不需要）
- 不要在 CI 跑完整 `pytest --slow`（要的是快速反馈，5 分钟内）
- 不要在 CI 用 `pip install .`（会丢失 dev deps）

---

### 4.2 Task P3-2 — PyPI 发布流水线（不实际发布）

**Why**：`pip install -e .` 不是给用户用的，是给开发者用的。要让路人 `pip install debug-mind` 一行装上。

**改什么**

1. 新建 `.github/workflows/release.yml`：
   ```yaml
   name: release
   on:
     workflow_dispatch:           # 只手动触发
       inputs:
         target:
           description: "test | prod"
           default: test
   jobs:
     publish:
       runs-on: ubuntu-latest
       environment: pypi          # 受保护环境
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with: { python-version: "3.12" }
         - run: pip install build twine
         - run: python -m build
         - name: publish to TestPyPI
           if: inputs.target == 'test'
           env: { TWINE_PASSWORD: ${{ secrets.TESTPYPI_TOKEN }} }
           run: twine upload --repository testpypi dist/*
         - name: publish to PyPI
           if: inputs.target == 'prod'
           env: { TWINE_PASSWORD: ${{ secrets.PYPI_TOKEN }} }
           run: twine upload dist/*
   ```
2. 版本号管理：保留 `pyproject.toml` 里的静态版本（不引 hatch-vcs 这种复杂工具），写一个 `scripts/bump_version.py` 同时更新 `pyproject.toml` 和 `src/debug_mind/__init__.py:__version__`。
3. `RELEASE.md` 文档：手工发版步骤（更新 CHANGELOG → bump_version → tag → push tag → dispatch workflow → test pypi 验证 → prod pypi）。
4. **第一次不要真发**。本 task 完成的标志是 dry-run 成功（`python -m build` 产出 wheel + sdist，`twine check dist/*` 通过），TestPyPI 实际发布留给维护者人工触发。

**验收**

- [ ] `python -m build` 本地能跑出 `dist/*.whl` 和 `*.tar.gz`
- [ ] `twine check dist/*` 全部 PASSED
- [ ] 解开 wheel，确认 `debug_mind/`、`evaluation/cases/*.yaml`、`evaluation/seed_cases/*.md` 都在
- [ ] `RELEASE.md` 步骤一条条可对照执行
- [ ] workflow YAML 通过 `actionlint` 校验

**不要做**

- 不要 push tag 触发自动发布——必须 `workflow_dispatch`
- 不要把 PyPI token 写到 workflow YAML
- 不要引 hatch-vcs / setuptools-scm（多一个魔法层，第一次发布反而难调试）

---

### 4.3 Task P3-3 — 贡献者文档与 issue / PR 模板

**Why**：新人想 PR，第一件事问的是"我该建什么分支？测试咋跑？commit message 啥规范？"——这些不写明白，PR 会自带分歧。

**改什么**

1. `CONTRIBUTING.md`：
   - 项目结构 1 张图（粘 `tree -L 2` 输出 + 简注）
   - 本地起步：`pip install -e .[dev]` → `pytest` → `debug-mind eval --search-only`
   - 分支策略：master 长期分支，PR 从 feature branch
   - Commit message：Conventional Commits（feat / fix / docs / refactor / test / chore），举两个例子
   - 测试要求：新增功能配套测试；`pytest --cov=src` 覆盖率不下降
   - 代码风格：ruff format 已配置；不写无关注释
   - 引用 REFACTOR_PLAN / PHASE_2_PLAN / PHASE_3_PLAN 作为项目演进读物
2. `CHANGELOG.md`：Keep a Changelog 1.1 格式，先写四个版本段落：
   ```
   ## [Unreleased]
   ## [0.1.0] - 2026-05-19  (Phase 1 完结)
     ### Added: 评测框架、反馈闭环、embedding 可插拔...
   ## [Phase 2 - pending]
   ## [Phase 3 - pending]
   ```
3. `.github/ISSUE_TEMPLATE/bug_report.yml` + `feature_request.yml` + `question.yml`（YAML 格式带表单字段）。bug_report 必填：复现步骤、期望、实际、`debug-mind --version`、Python 版本、OS。
4. `.github/PULL_REQUEST_TEMPLATE.md`：勾选框 checklist —— 测试 / 文档 / CHANGELOG / 不破坏 eval。
5. `CODE_OF_CONDUCT.md`：直接抄 Contributor Covenant 2.1（GitHub 一键模板）。
6. `SECURITY.md`：怎么报安全问题（email 或 private issue），SLA 写明 7 天回复。

**验收**

- [ ] 上传 GitHub 后，"New issue" 弹出表单（不是空 textarea）
- [ ] 提 PR 自动套用模板
- [ ] CHANGELOG 至少 4 个段落
- [ ] CONTRIBUTING 步骤照做能在 5 分钟跑起项目（在干净 venv 实测）

**不要做**

- 不要把 CHANGELOG 写满"我做了哪些事"——只记 user-facing 变更
- 不要在模板里要求过多字段，path of least resistance

---

### 4.4 Task P3-4 — README demo + 架构图

**Why**：3 秒决定一个路人是关掉 tab 还是 star。文字 README 没几个人耐心看到下面。

**改什么**

1. 录一段 30 秒 asciinema：从 `pip install debug-mind` → `debug-mind diagnose "NPE in UserService"` → 看到诊断结果 → `debug-mind list`。
   - 用 `asciinema rec demo.cast`，存到 `docs/demo.cast`
   - 用 `agg` 或 `svg-term-cli` 转 SVG，放 `docs/demo.svg`，README 头部插入
   - 不在 CI 里跑（录制是一次性手工动作）
2. README 头部追加 "Why DebugMind"（中英双语，3-4 段）：
   - 一句话价值：基于记忆的 bug 诊断 Agent，每解一个 bug 都让下次更快
   - 三件别人没做的事：experiential memory + 自评 benchmark + MCP server
   - 一段示例诊断输出（贴 demo 截屏文字）
3. `docs/ARCHITECTURE.md`：
   - 一张 mermaid 时序图：用户 → CLI → DiagnosticAgent → MemoryStore.search → Anthropic API → tool loop → save → markdown + chroma
   - 一张 mermaid 模块依赖图：`debug_mind.cli` → `debug_mind.agent` → `debug_mind.memory` / `debug_mind.tools` / `debug_mind.skills`
   - 各模块一句话职责
4. `docs/DEVELOPMENT.md`：
   - 怎么加一个新工具（schema → executor → MCP 同步）
   - 怎么加一个新 embedding provider（实现 EmbeddingFunction protocol）
   - 怎么加一个新 reranker
   - 引用现有 voyage / LLMReranker 作示例
5. `docs/EVALUATION.md`：
   - benchmark 数据集结构说明
   - 怎么加新 case（yaml schema + seed markdown）
   - 现有指标（hit@k / MRR / keyword recall）含义
   - 跑 eval 命令清单

**验收**

- [ ] README 在 GitHub 网页上滚一屏内能看到 demo.svg
- [ ] mermaid 图在 GitHub markdown 渲染正常（不要用本地图床）
- [ ] ARCHITECTURE / DEVELOPMENT / EVALUATION 三份文档每份 ≤ 200 行（不堆字）
- [ ] 删掉 README 后，开发流程仍能从 docs/ + CONTRIBUTING 找回

**不要做**

- 不要用付费 / 自部署图床（mermaid 原生支持就够了）
- 不要在 README 写 30 段 "feature list"——demo + 3 个差异化卖点足够
- 不要重复 REFACTOR_PLAN 已有的内容；docs/ARCHITECTURE 是"为什么这样设计"，REFACTOR_PLAN 是"接下来做什么"

---

### 4.5 Task P3-5 — Benchmark 扩到 50+ 真实 case

**Why**：12 个合成 case 只能证明代码没崩。要讲"加了记忆真的更快"，需要数量 + 真实性。

**改什么**

1. 在 `evaluation/cases/` 扩到 ≥ 50 个 case，要求：
   - 来源：每个 case 必须含 `source:` 字段指向公开链接（GitHub issue / Stack Overflow / 公开博客）。无 source 的 case 标 `synthetic: true`。
   - 多样性矩阵覆盖：
     - 语言：Java ≥ 10、Python ≥ 10、Node/JS ≥ 8、Go ≥ 5、其它 ≥ 5
     - 类别：NPE / OOM / 死锁 / 连接池 / 配置 / 异步 / 依赖循环 / 序列化 / 编码 / 时区 各 ≥ 3
   - 配套 seed case（`evaluation/seed_cases/`）跟着扩，保持 benchmark ↔ seed 配对率 ≥ 70%
2. 给 `evaluation/dataset.py` 的 `BenchmarkCase` 加 optional field：
   ```python
   source: str | None = None              # 公开链接
   synthetic: bool = False                # 是否手造
   language: str | None = None            # 主语言
   category: str | None = None            # 错误分类
   ```
3. 跑 `debug-mind eval --search-only` 得到新 baseline，写进 `docs/EVALUATION.md` 的"Baseline scores"小节。
4. `evaluation/README.md`：说明数据集来源、版权（每条都是公开内容，原文链接保留），怎么贡献新 case。

**验收**

- [ ] `ls evaluation/cases/*.yaml | wc -l` ≥ 50
- [ ] 每个 yaml 都能 `load_case` 成功
- [ ] 至少 35 个 case 有 `source:` 字段
- [ ] eval 全集跑通，分数写进 docs/EVALUATION.md（数字会下降——50 case 比 12 case 真实，不要因此回头注水）
- [ ] 三大语言 + 十大类别覆盖到位

**不要做**

- 不要从需要授权的来源（公司内部 wiki / 付费课程）抄 case
- 不要为了凑数复制粘贴改一两个字
- 不要因为分数下降回头改 keyword 列表"对齐"

---

## 5. Phase 4 / 5 占位（本轮不做）

- Phase 4 = 能力增强（LLM provider 抽象、SQLite、tree-sitter、Web UI、插件机制）
- Phase 5 = 高阶记忆（衰减 / 再验证、记忆图谱、协作冲突解决、多人 RBAC）

---

## 6. 执行 AI 自检清单（提交前）

- [ ] 既有测试和 eval 数字都没变（除 P3-5 的 eval 因数据集扩展会变）
- [ ] CI 工作流在 GitHub Actions 实际跑通至少一次（push 一个 trivial commit 验证）
- [ ] `.github/` 下文件齐全：workflows / ISSUE_TEMPLATE / PULL_REQUEST_TEMPLATE
- [ ] README 头部有 badge + demo SVG，既有章节未删
- [ ] CONTRIBUTING / CHANGELOG / CODE_OF_CONDUCT / SECURITY 都存在
- [ ] docs/ 下 ARCHITECTURE / DEVELOPMENT / EVALUATION / embeddings 四份齐全
- [ ] `evaluation/cases/*.yaml` ≥ 50 个

---

## 7. 评审者会检查的具体项

1. **P3-1**：在 fork 上提个测试 PR 看 CI 是不是 8 jobs 全跑 + 5 分钟内完成
2. **P3-2**：本地 `python -m build && twine check dist/*` 必须通过；解开 wheel 验证 evaluation/ 数据文件在
3. **P3-3**：随便点 New Issue 看表单；CHANGELOG 写法是否合规（Keep a Changelog 格式）
4. **P3-4**：README 在手机端 GitHub 网页打开能否看到 demo（SVG 在窄屏要 OK）
5. **P3-5**：随机抽 5 个 case 看 source 链接是否真的指向公开内容；多样性矩阵抽查

---

## 8. 执行日志

格式：`[YYYY-MM-DDTHH:MM] [TASK P3-X] 简述 + 关键数字 + 取舍`

```
[填] [BASELINE] pytest X 通过；eval search-only hit@1=0.92；README 行数 Y
```

---

## 9. 提交建议

每个 P3 任务一个 commit。P3-5 由于 case 文件多，可以拆成多个 commit（按语言 / 按类别）。commit message 例：

```
chore(ci): add GitHub Actions test + lint workflows (Task P3-1)

- matrix: 2 OS × 4 Python versions
- runs ruff + pytest + eval --search-only
- adds README badges
```

不要合并到 master，停在本分支等评审。
