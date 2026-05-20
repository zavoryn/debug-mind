# DebugMind Phase 5 — 高阶记忆

> 这是 **Phase 5 工作单**。Phase 1-4 已完成；本阶段让记忆系统具备「遗忘」、「自我纠错」和「知识关联」能力。

---

## 0. 工作纪律

1. 从基线开始：`pytest -v` 全绿，`debug-mind eval --search-only` 跑通
2. 一次一个任务，做完测试 → commit（不 push）
3. 不破坏 Phase 1-4 的接口和 schema
4. 新增功能通过 env var 控制默认行为

---

## 1. 背景

当前记忆系统的问题：
- 所有 case 永远保留，错误的 case 也会一直占坑
- 验证过的 case 和未验证的 case 权重差异只有 0.7 vs 1.0
- 没有记忆之间的关联（"这个 bug 是那个 bug 的变体"）
- 没有多人协作时的冲突处理

---

## 2. 任务

### P5-1 — 记忆衰减

**Why**：永远不用的 case 永远占坑 → 搜索质量随时间下降。

**改什么**
- `MemoryStats` 加 `stale_count`、`avg_hit_rate` 字段
- `MemoryStore.decay(days=30)` 方法：标记超过 N 天未使用的 case 为 stale
- `search()` 对 stale case 降低权重 0.5×
- `debug-mind decay --days 30 --dry-run` CLI

### P5-2 — 再验证机制

**Why**：一次性 verify 后永远信任 → 环境变了 case 可能已过期。

**改什么**
- `BugCase` 加 `last_verified_at: datetime | None`、`verify_count: int`
- `MemoryStore.reverify(days=90)` 方法：列出超过 N 天未重新验证的 case
- `debug-mind reverify --days 90` CLI
- verified case 超过 180 天未再验证 → 降级权重

### P5-3 — 记忆图谱

**Why**：case 之间有引用关系但无法查询 → "相似 bug" 发现能力弱。

**改什么**
- `MemoryStore.link(case_a, case_b, relation)` 方法
- 关系类型：`variant`、`caused_by`、`fixed_by`、`related`
- `search()` 结果中自动展开关联 case（最多 2 跳）
- `debug-mind link <id-a> <id-b> --relation variant` CLI

### P5-4 — 多人协作冲突

**Why**：两人同时 verify 同一个 case → 后写覆盖先写。

**改什么**
- `BugCase` 加 `version: int` 乐观锁字段
- `save()` 检测版本冲突（markdown 中 version 字段与内存不一致）
- 冲突时返回 `ConflictError` 而非静默覆盖
- CLI 显示冲突信息并提供 `--force` 选项

---

## 3. 提交建议

每个 P5 任务一个 commit，明天一起 push。
