# Gap-Close Workflow 设计文档

> 日期: 2026-07-03 | 状态: Design Approved

## 概述

创建一个自动化 Workflow，循环执行"差距审查 → 计划 → 实施修复"，直到代码实现与设计规范之间无功能完整性差距。采用 loop-until-dry 终止模式（连续两轮无新发现）。

**核心目标**: 确保 `myagent/` 的代码实现完全符合 `docs/superpowers/specs/2026-07-02-myagentcli-design.md` 设计规范。

---

## 一、整体架构

### Workflow 名称

`close-design-gaps`

### 循环流程

```
┌─────────────────────────────────────────────────────────┐
│  dryRounds = 0, totalRounds = 0                         │
│  MAX_ROUNDS = 10                                        │
│                                                          │
│  while (dryRounds < 2 AND totalRounds < MAX_ROUNDS):    │
│    totalRounds++                                         │
│                                                          │
│    ┌─ Phase 1: Review ───────────────────────────┐      │
│    │ spawn 子Agent (reviewer)                      │      │
│    │   1. Read 设计规范                            │      │
│    │   2. Explore 所有代码文件 (myagent/**/*.py)   │      │
│    │   3. 逐模块对比 → 识别差距 (missing/          │      │
│    │      incomplete/deviation)                    │      │
│    │   4. Write 差距报告 →                        │      │
│    │      docs/gap-reports/                        │      │
│    │      YYYY-MM-DD-gap-round-N.md                │      │
│    │   5. git add + git commit 差距报告            │      │
│    │   6. Return 结构化结果 (GAPS_SCHEMA)          │      │
│    └──────────────────────────────────────────────┘      │
│                                                          │
│    if gaps.length == 0:                                  │
│      dryRounds++; continue                               │
│    dryRounds = 0                                         │
│                                                          │
│    ┌─ Phase 2: Plan & Implement ──────────────────┐     │
│    │ spawn 子Agent (fixer)                          │      │
│    │   1. Read 差距报告                             │      │
│    │   2. 调用 superpowers:writing-plans 制定计划  │      │
│    │   3. Write 修复计划 →                         │      │
│    │      docs/gap-reports/                         │      │
│    │      YYYY-MM-DD-gap-round-N-fix-plan.md       │      │
│    │   4. git commit 修复计划                       │      │
│    │   5. 调用 superpowers:subagent-driven-        │      │
│    │      development 逐任务实施                    │      │
│    │   6. 每个任务完成后 git commit                │      │
│    │   7. Return 结构化结果 (FIX_RESULT_SCHEMA)    │      │
│    └──────────────────────────────────────────────┘      │
│                                                          │
│  if totalRounds >= MAX_ROUNDS:                           │
│    log("达到最大轮次，请手动检查剩余差距。")              │
│  else:                                                   │
│    log("完成！连续 2 轮无新差距。")                       │
└─────────────────────────────────────────────────────────┘
```

---

## 二、数据结构与 Schema

### 差距报告文件格式

```markdown
<!-- docs/gap-reports/YYYY-MM-DD-gap-round-N.md -->

---
date: YYYY-MM-DD
round: N
total_gaps: M
design_spec: docs/superpowers/specs/2026-07-02-myagentcli-design.md
---

# Gap Report — Round N

## Summary
- 缺失功能 (missing): X
- 不完整实现 (incomplete): Y
- 偏离设计 (deviation): Z

## Gap 1: [severity] 标题
- **Category**: missing | incomplete | deviation
- **Section**: 设计规范对应章节
- **Files**: 涉及的代码文件
- **Description**: 具体差距描述
- **Expected**: 设计规范要求的行为
- **Actual**: 当前代码实际行为

## Gap 2: ...
```

### 修复计划文件格式

```markdown
<!-- docs/gap-reports/YYYY-MM-DD-gap-round-N-fix-plan.md -->

---
date: YYYY-MM-DD
round: N
gaps_to_fix: K
source_report: docs/gap-reports/YYYY-MM-DD-gap-round-N.md
---

# Fix Plan — Round N

## Summary
修复 K 个差距，涉及 M 个文件。

## Task 1: [描述]
- **Gap IDs**: gap-1, gap-2
- **Files**: 修改的文件路径
- **Approach**: 修复方式描述
- **Verification**: 如何验证修复完成
```

### Workflow 子Agent 返回 Schema

**Review Agent (GAPS_SCHEMA):**

```json
{
  "type": "object",
  "properties": {
    "gaps": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "id": { "type": "string" },
          "severity": { "enum": ["critical", "high", "medium", "low"] },
          "category": { "enum": ["missing", "incomplete", "deviation"] },
          "section": { "type": "string" },
          "files": { "type": "array", "items": { "type": "string" } },
          "description": { "type": "string" },
          "expected": { "type": "string" },
          "actual": { "type": "string" }
        },
        "required": ["id", "severity", "category", "section", "description", "expected", "actual"]
      }
    },
    "total_gaps": { "type": "integer" },
    "summary": { "type": "string" }
  },
  "required": ["gaps", "total_gaps", "summary"]
}
```

**Plan+Implement Agent (FIX_RESULT_SCHEMA):**

```json
{
  "type": "object",
  "properties": {
    "gaps_fixed": { "type": "integer" },
    "gaps_skipped": { "type": "integer" },
    "details": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "gap_id": { "type": "string" },
          "status": { "enum": ["fixed", "skipped", "partial"] },
          "commits": { "type": "array", "items": { "type": "string" } },
          "notes": { "type": "string" }
        },
        "required": ["gap_id", "status", "notes"]
      }
    },
    "summary": { "type": "string" }
  },
  "required": ["gaps_fixed", "gaps_skipped", "details", "summary"]
}
```

---

## 三、目录结构

```
docs/
├── gap-reports/                          ← 差距报告持久化目录（新建）
│   ├── YYYY-MM-DD-gap-round-1.md         ← 第1轮差距报告
│   ├── YYYY-MM-DD-gap-round-1-fix-plan.md ← 第1轮修复计划
│   ├── YYYY-MM-DD-gap-round-2.md
│   ├── YYYY-MM-DD-gap-round-2-fix-plan.md
│   └── ...
└── superpowers/
    ├── specs/
    │   ├── 2026-07-02-myagentcli-design.md   ← 输入：设计规范
    │   └── 2026-07-03-gap-close-workflow-design.md ← 本设计文档
    └── plans/
        ├── 2026-07-03-myagentcli-implementation.md
        └── 2026-07-03-audit-fix-plan.md
```

---

## 四、异常处理与边界条件

### 异常场景矩阵

| 场景 | 处理策略 |
|------|----------|
| 子Agent 超时/失败 | 记录失败信息，该轮计为 dryRounds++（不阻塞循环），下轮重新审查 |
| 修复引入新差距 | 下一轮 Review 自动发现，进入再修复循环 |
| 连续 3 轮 dryRounds=0（一直有差距但修不完） | 通过 `log()` 输出警告，提示用户检查是否陷入死循环 |
| git 工作区脏（上一轮提交失败残留） | 子Agent 在 commit 前检查 `git status --porcelain`，无变更则跳过 commit |
| 设计规范文件不存在 | Review Agent 立即报错退出，不进入循环 |
| 差距过大（>20 个） | 按 severity 排序，优先修复 critical/high，其余标记延期到下一轮 |
| 修复计划文件已存在（同轮） | 覆盖写入 |

### 死循环保护

- **硬上限**: 最多 10 轮，超出后输出警告并停止
- **dryRounds 计数器**: 连续 2 轮无新差距才终止，防止修复引入新问题后立即停止
- **连续无进展检测**: 连续 3 轮 `dryRounds=0` 时输出 `log()` 警告

### Git 提交规范

遵循约定式提交格式（Conventional Commits）：

| 类型 | 格式 | 示例 |
|------|------|------|
| 差距报告 | `docs: gap report round N — M gaps found` | `docs: gap report round 1 — 5 gaps found` |
| 修复计划 | `docs: fix plan for round N — K gaps to fix` | `docs: fix plan for round 1 — 5 gaps to fix` |
| 代码修复 | `fix(<module>): <description>` | `fix(agent): implement multi-turn ReAct loop` |
| 新增功能 | `feat(<module>): <description>` | `feat(cli): add status bar sub-agent display` |

### 子Agent 差异化配置

| Agent | Phase | 推荐配置 | 原因 |
|-------|-------|----------|------|
| Reviewer | Review | effort: 'high' | 需要深度代码审查，对比设计规范 |
| Fixer | Plan & Implement | effort: 'max' | 需要制定计划 + 实施修复，最复杂任务 |

---

## 五、验证方式

Workflow 运行完成后，手动验证：

1. `docs/gap-reports/` 目录下有完整的差距报告和修复计划链
2. `git log` 显示每轮有对应提交记录
3. 最后一轮 Review 返回 `total_gaps: 0`
4. `pytest tests/ -v` 全部通过
5. 代码实现与设计规范功能对齐（最终状态验证）

---

## 六、Workflow 脚本伪代码

```js
export const meta = {
  name: 'close-design-gaps',
  description: 'Loop: review code vs design spec → plan → fix → repeat until 2 dry rounds',
  phases: [
    { title: 'Review', detail: 'Sub-agent reviews code vs design spec, outputs gap report' },
    { title: 'Plan & Implement', detail: 'Sub-agent writes fix plan and implements all fixes' },
  ],
}

const DESIGN_SPEC = 'docs/superpowers/specs/2026-07-02-myagentcli-design.md'
const GAP_REPORTS_DIR = 'docs/gap-reports'
const MAX_ROUNDS = 10

const GAPS_SCHEMA = { /* as defined in §二 */ }
const FIX_RESULT_SCHEMA = { /* as defined in §二 */ }

let dryRounds = 0
let totalRounds = 0
let consecutiveNoProgress = 0
const allGaps = []

while (dryRounds < 2 && totalRounds < MAX_ROUNDS) {
  totalRounds++

  // ── Phase 1: Review ──
  phase('Review')
  const review = await agent(
    `Review all code vs design spec at ${DESIGN_SPEC}. ...`,
    { label: `review-round-${totalRounds}`, phase: 'Review', schema: GAPS_SCHEMA, effort: 'high' }
  )

  if (!review) { dryRounds++; continue }

  if (review.total_gaps === 0) {
    dryRounds++
    consecutiveNoProgress = 0
    log(`Round ${totalRounds}: 0 gaps found. dryRounds=${dryRounds}/2`)
    continue
  }

  dryRounds = 0
  consecutiveNoProgress++
  allGaps.push(...review.gaps)
  log(`Round ${totalRounds}: ${review.total_gaps} gaps found.`)

  if (consecutiveNoProgress >= 3) {
    log(`WARNING: ${consecutiveNoProgress} consecutive rounds with gaps but no convergence. Check for deadlock.`)
  }

  // ── Phase 2: Plan & Implement ──
  phase('Plan & Implement')
  const fixResult = await agent(
    `Read the gap report and fix all gaps. ...`,
    { label: `fix-round-${totalRounds}`, phase: 'Plan & Implement', schema: FIX_RESULT_SCHEMA, effort: 'max' }
  )

  if (fixResult) {
    log(`Round ${totalRounds}: fixed ${fixResult.gaps_fixed}, skipped ${fixResult.gaps_skipped}`)
  }
}

if (totalRounds >= MAX_ROUNDS) {
  log(`Reached max rounds (${MAX_ROUNDS}). Remaining gaps: ${allGaps.length}. Manual review needed.`)
} else {
  log(`Done! 2 consecutive rounds with no new gaps after ${totalRounds} rounds.`)
}
```

---

| 组件 | 选型 | 原因 |
|------|------|------|
| 执行引擎 | Workflow 工具 (while loop) | 完全自动化，状态在脚本内存中追踪 |
| 审查 Agent | agent() + GAPS_SCHEMA + effort='high' | 需要深度代码审查和结构化输出 |
| 修复 Agent | agent() + FIX_RESULT_SCHEMA + effort='max' | 制定计划 + 实施修复，最复杂任务 |
| 持久化 | Markdown 文件 (docs/gap-reports/) | 人类可读，Git 可追溯 |
| Git 提交 | 子Agent 内通过 Bash 工具执行 | 每个任务完成后及时提交，粒度细 |
| 终止条件 | loop-until-dry (dryRounds >= 2) | 防止修复引入新问题后过早停止 |
| 死循环保护 | MAX_ROUNDS=10 + consecutiveNoProgress 检测 | 防止无限循环消耗 token |
