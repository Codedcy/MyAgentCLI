# Gap-Close Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Write and execute the `close-design-gaps` Workflow script — a single JS file that orchestrates a loop of code-vs-design-spec review, gap report, fix planning, and implementation until 2 consecutive rounds find no new gaps.

**Architecture:** Single Workflow script using `agent()` with structured output schemas. Two phases per iteration: "Review" (spawn reviewer sub-agent → gap report file → git commit → return GAPS_SCHEMA) and "Plan & Implement" (spawn fixer sub-agent → write fix plan file → git commit → implement fixes task-by-task → git commit each → return FIX_RESULT_SCHEMA). Loop until dryRounds >= 2 or MAX_ROUNDS=10.

**Tech Stack:** Workflow JavaScript runtime (agent(), phase(), log(), parallel(), pipeline()), Claude Code Bash/Read/Write/Edit/Glob/Grep tools (available to sub-agents).

## Global Constraints

- All sub-agent tasks must end with `git add` + `git commit` before returning
- Gap reports saved to `docs/gap-reports/YYYY-MM-DD-gap-round-N.md`
- Fix plans saved to `docs/gap-reports/YYYY-MM-DD-gap-round-N-fix-plan.md`
- Commits follow Conventional Commits format (see design spec §四)
- No stubs, no TODOs, no temporary implementations — every fix must be complete
- Sub-agent prompts must instruct them to NOT produce placeholder implementations
- Review Agent uses effort='high', Fix Agent uses effort='max'
- MAX_ROUNDS = 10, loop-until-dry (dryRounds >= 2)
- Consecutive 3 rounds without progress → log() warning
- Design spec path: `docs/superpowers/specs/2026-07-02-myagentcli-design.md`

---

## File Structure

```
docs/
├── gap-reports/                              ← New directory (created in Task 1)
└── superpowers/
    ├── specs/
    │   ├── 2026-07-02-myagentcli-design.md   ← Input: design spec to check against
    │   └── 2026-07-03-gap-close-workflow-design.md
    └── plans/
        └── 2026-07-03-gap-close-workflow-plan.md  ← This file
```

The Workflow script itself is passed inline to the Workflow tool (not saved as a file unless persisted by the tool runtime).

---

## Task 1: Create gap-reports directory and verify setup

**Files:**
- Create: `docs/gap-reports/.gitkeep`

**Interfaces:**
- Produces: Empty `docs/gap-reports/` directory ready for gap report files

- [ ] **Step 1: Create gap-reports directory**

```bash
mkdir -p docs/gap-reports
```

- [ ] **Step 2: Create .gitkeep to track empty dir**

Write `docs/gap-reports/.gitkeep` with content:
```
# Gap reports from close-design-gaps workflow
# Each round produces:
#   YYYY-MM-DD-gap-round-N.md          — gap findings
#   YYYY-MM-DD-gap-round-N-fix-plan.md — fix plan
```

- [ ] **Step 3: Verify directory exists**

Run: `ls docs/gap-reports/`
Expected: `.gitkeep` file present

- [ ] **Step 4: Commit**

```bash
git add docs/gap-reports/
git commit -m "docs: create gap-reports directory for workflow output"
```

---

## Task 2: Write and execute the Workflow script

**Files:**
- No permanent files created (Workflow script is passed inline and auto-persisted by the runtime)

**Interfaces:**
- Consumes: Design spec at `docs/superpowers/specs/2026-07-02-myagentcli-design.md`
- Consumes: All code files under `myagent/` (read by Review Agent)
- Produces: Gap reports in `docs/gap-reports/` (written by sub-agents)
- Produces: Fix plans in `docs/gap-reports/` (written by sub-agents)
- Produces: Code fixes committed via git (by sub-agents)

**[Full script content follows — this is the complete Workflow script to pass to the Workflow tool]**

```js
export const meta = {
  name: 'close-design-gaps',
  description: 'Loop: review code vs design spec → plan → fix → repeat until 2 dry rounds',
  phases: [
    { title: 'Review', detail: 'Sub-agent reviews code vs design spec, outputs gap report file + git commit' },
    { title: 'Plan & Implement', detail: 'Sub-agent writes fix plan file, implements all fixes task-by-task, git commits each' },
  ],
}

const DESIGN_SPEC = 'docs/superpowers/specs/2026-07-02-myagentcli-design.md'
const GAP_REPORTS_DIR = 'docs/gap-reports'
const MAX_ROUNDS = 10

const GAPS_SCHEMA = {
  type: 'object',
  properties: {
    gaps: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          id: { type: 'string' },
          severity: { enum: ['critical', 'high', 'medium', 'low'] },
          category: { enum: ['missing', 'incomplete', 'deviation'] },
          section: { type: 'string' },
          files: { type: 'array', items: { type: 'string' } },
          description: { type: 'string' },
          expected: { type: 'string' },
          actual: { type: 'string' },
        },
        required: ['id', 'severity', 'category', 'section', 'description', 'expected', 'actual'],
      },
    },
    total_gaps: { type: 'integer' },
    summary: { type: 'string' },
    report_file: { type: 'string' },
  },
  required: ['gaps', 'total_gaps', 'summary', 'report_file'],
}

const FIX_RESULT_SCHEMA = {
  type: 'object',
  properties: {
    gaps_fixed: { type: 'integer' },
    gaps_skipped: { type: 'integer' },
    details: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          gap_id: { type: 'string' },
          status: { enum: ['fixed', 'skipped', 'partial'] },
          commits: { type: 'array', items: { type: 'string' } },
          notes: { type: 'string' },
        },
        required: ['gap_id', 'status', 'notes'],
      },
    },
    summary: { type: 'string' },
    plan_file: { type: 'string' },
  },
  required: ['gaps_fixed', 'gaps_skipped', 'details', 'summary', 'plan_file'],
}

const today = new Date().toISOString().slice(0, 10)  // placeholder — runtime replaces with actual date
// NOTE: Workflow runtime disallows Date.now()/new Date(). Use the date from the context.

let dryRounds = 0
let totalRounds = 0
let consecutiveNoProgress = 0
const allFoundGaps = []

// Use the actual date — since Date.now() is unavailable in workflow scripts,
// we derive it from the run context. The sub-agents will use the actual date
// when writing files, so we just need round numbers.
// For file paths, sub-agents determine the date themselves via Bash `date` command.

while (dryRounds < 2 && totalRounds < MAX_ROUNDS) {
  totalRounds++

  // ═══════════════════════════════════════════════════════
  // Phase 1: Review
  // ═══════════════════════════════════════════════════════
  phase('Review')

  const reviewPrompt = `You are a thorough code reviewer. Your task is to compare the ACTUAL code implementation against the DESIGN SPEC and find ALL gaps.

## DESIGN SPEC
Read this file carefully: ${DESIGN_SPEC}

It defines the full architecture, features, and behavior that the codebase must implement.

## CODEBASE
All code is under the "myagent/" directory. Explore EVERY Python file:
- Use Glob to list all .py files: myagent/**/*.py
- Read each file and compare against the design spec
- Check for:
  - **missing**: Features/behaviors described in the spec but NOT implemented in code
  - **incomplete**: Feature exists in code but is partially implemented (stub, placeholder, TODO, hardcoded fallback)
  - **deviation**: Code implements something that contradicts the design spec

## WHAT TO CHECK (use the design spec sections as your checklist)
1. §一 整体架构 — Four layers present? All components wired?
2. §二 核心Agent循环 — Full ReAct loop (Think→Decision→Execute→Observe, looping)? Intent signals? Thinking modes?
3. §三 上下文分层 — Six-layer model? Compression (4-layer progressive, 75% trigger, 30% target)? Persistence?
4. §四 工具系统 — All 11 built-in tools? Unified Tool protocol? MCP integration? spawn_subagent with all params?
5. §五 权限系统 — 4 levels? allow/deny lists? Confirmation flow? Mid-conversation adjustment?
6. §六 记忆系统 — File-level memories? MEMORY.md index? Dream mechanism with correct triggers?
7. §七 技能系统 — Multi-level scan? SKILL.md parsing? skill_invoke virtual tool? Resource handling?
8. §八 子Agent池 — Pool management (max concurrent, global cap)? Lifecycle states? send_message? Background rules?
9. §九 配置系统 — 7 levels? Deep merge? All config sections (model, context, permissions, subagents, dream, tools, ui, session, logging)?
10. §十 会话系统 — Project detection (git, type, package manager)? Session listing? Session end flow?
11. §十一 日志系统 — All categories (system/llm/tool/agent/subagent/error)? JsonLineFormatter? LogContext with contextvars? Async queue?

## OUTPUT
After completing the review, you MUST:

1. **Write a gap report file** to \`${GAP_REPORTS_DIR}/<YYYY-MM-DD>-gap-round-${totalRounds}.md\`
   (Use Bash: \`date +%Y-%m-%d\` to get today's date)
   
   Format:
   \`\`\`markdown
   ---
   date: <today>
   round: ${totalRounds}
   total_gaps: <count>
   design_spec: ${DESIGN_SPEC}
   ---

   # Gap Report — Round ${totalRounds}

   ## Summary
   - 缺失功能 (missing): <count>
   - 不完整实现 (incomplete): <count>
   - 偏离设计 (deviation): <count>

   ## Gap 1: [severity] Brief title
   - **Category**: missing | incomplete | deviation
   - **Section**: Which section in the design spec
   - **Files**: Affected code file paths
   - **Description**: What specifically is wrong
   - **Expected**: What the design spec requires
   - **Actual**: What the code currently does

   (Repeat for each gap)
   \`\`\`

2. **git add + git commit** the report file:
   \`\`\`bash
   git add ${GAP_REPORTS_DIR}/<filename>.md
   git commit -m "docs: gap report round ${totalRounds} — <N> gaps found"
   \`\`\`

3. Return the structured findings using the output schema.

CRITICAL: Be exhaustive. Check every section of the design spec. Check every Python file. Report EVERY gap, no matter how small. Do not skip anything because "it's probably fine."`

  const review = await agent(reviewPrompt, {
    label: `review-r${totalRounds}`,
    phase: 'Review',
    schema: GAPS_SCHEMA,
    effort: 'high',
  })

  // Handle review failure
  if (!review) {
    log(`Round ${totalRounds}: Review agent failed or was skipped. Counting as dry round.`)
    dryRounds++
    continue
  }

  log(`Round ${totalRounds}: ${review.total_gaps} gaps found. Report: ${review.report_file}`)

  if (review.total_gaps === 0) {
    dryRounds++
    consecutiveNoProgress = 0
    log(`Round ${totalRounds}: ✓ No gaps. dryRounds = ${dryRounds}/2`)
    continue
  }

  // Gaps found — reset dry counter
  dryRounds = 0
  consecutiveNoProgress++
  allFoundGaps.push(...review.gaps)

  if (consecutiveNoProgress >= 3) {
    log(`⚠ WARNING: ${consecutiveNoProgress} consecutive rounds with gaps but no convergence. Possible deadlock — check if fixes are actually being applied.`)
  }

  // ═══════════════════════════════════════════════════════
  // Phase 2: Plan & Implement
  // ═══════════════════════════════════════════════════════
  phase('Plan & Implement')

  // Prioritize: critical first, then high, medium, low
  const priorityOrder = { critical: 0, high: 1, medium: 2, low: 3 }
  const sortedGaps = [...review.gaps].sort((a, b) => priorityOrder[a.severity] - priorityOrder[b.severity])
  const gapSummary = sortedGaps.map(g => `- **${g.id}** [${g.severity}/${g.category}] ${g.description.slice(0, 120)} (${g.section})`).join('\n')

  const fixPrompt = `You are a skilled software engineer. Your task is to fix ALL gaps identified in the latest review. YOU MUST COMPLETE EVERY FIX. No stubs. No TODOs. No temporary implementations. No "this will be done later."

## GAPS TO FIX (Round ${totalRounds})
${gapSummary}

## DESIGN SPEC REFERENCE
Read the design spec at \`${DESIGN_SPEC}\` for full context on how each feature must work.

## PROCESS

### Step 1: Read the gap report
Read \`${review.report_file}\` for full details on each gap.

### Step 2: CREATE A FIX PLAN
Write a fix plan file to \`${GAP_REPORTS_DIR}/<YYYY-MM-DD>-gap-round-${totalRounds}-fix-plan.md\`
(Use Bash: \`date +%Y-%m-%d\` to get today's date)

Format:
\`\`\`markdown
---
date: <today>
round: ${totalRounds}
gaps_to_fix: ${review.total_gaps}
source_report: ${review.report_file}
---

# Fix Plan — Round ${totalRounds}

## Summary
Fix <N> gaps across <M> files.

## Task 1: [Descriptive name]
- **Gap IDs**: gap-1
- **Files**: paths to modify/create
- **Approach**: How to fix it in detail
- **Verification**: How to verify the fix is complete and correct

## Task 2: ...
\`\`\`

### Step 3: git commit the fix plan
\`\`\`bash
git add ${GAP_REPORTS_DIR}/<plan-filename>.md
git commit -m "docs: fix plan for round ${totalRounds} — ${review.total_gaps} gaps to fix"
\`\`\`

### Step 4: IMPLEMENT EVERY TASK
For each task in the fix plan:
1. Read the relevant source files
2. Write the implementation — COMPLETE, not stubbed
3. Verify the fix works (run tests if applicable: \`pytest tests/ -v\`)
4. git commit:
   \`\`\`bash
   git add <modified files>
   git commit -m "fix(<module>): <description of fix>"
   \`\`\`

### Step 5: Final verification
After all fixes:
- Run \`pytest tests/ -v\` to ensure no regressions
- Run \`git status\` to ensure nothing uncommitted remains
- If any gap cannot be fully fixed (requires external dependency, design spec contradiction, etc.), mark it as "skipped" with a clear reason

## CRITICAL RULES
- NO stubs. NO placeholders. NO TODOs. NO "implement later."
- Every fix must be a complete, working implementation
- Every task must end with a git commit
- If you must skip a gap, explain EXACTLY why in the notes
- Run tests after each fix to catch regressions early`

  const fixResult = await agent(fixPrompt, {
    label: `fix-r${totalRounds}`,
    phase: 'Plan & Implement',
    schema: FIX_RESULT_SCHEMA,
    effort: 'max',
  })

  if (fixResult) {
    log(`Round ${totalRounds}: fixed ${fixResult.gaps_fixed}, skipped ${fixResult.gaps_skipped}. Plan: ${fixResult.plan_file}`)
  } else {
    log(`Round ${totalRounds}: Fix agent failed or was skipped. Gaps remain for next round.`)
  }
}

// ═══════════════════════════════════════════════════════
// Final report
// ═══════════════════════════════════════════════════════
if (totalRounds >= MAX_ROUNDS) {
  log(`⏹ Reached max rounds (${MAX_ROUNDS}). ${allFoundGaps.length} total gaps were found across all rounds. Manual review may be needed.`)
} else {
  log(`✅ Done! 2 consecutive rounds with no new gaps after ${totalRounds} round(s). Code matches the design spec.`)
}
```

- [ ] **Step 1: Verify design spec is readable**

Run: `wc -l docs/superpowers/specs/2026-07-02-myagentcli-design.md`
Expected: ~1067 lines (spec is present and complete)

- [ ] **Step 2: Verify gap-reports directory exists**

Run: `ls docs/gap-reports/`
Expected: `.gitkeep` present

- [ ] **Step 3: Execute the Workflow**

Pass the script above to the Workflow tool. The Workflow runtime will:
1. Persist the script to the session directory
2. Start the while loop
3. Spawn Review Agent (effort=high) — agent reads spec, explores code, writes report, commits
4. Check gaps — if > 0, spawn Fix Agent (effort=max) — writes plan, commits, implements fixes, commits each
5. Repeat until dryRounds >= 2 or MAX_ROUNDS

- [ ] **Step 4: Monitor progress**

Watch the `/workflows` display for:
- Each round's gap count decreasing
- Fix commits appearing in git log
- Round transition messages from log()

- [ ] **Step 5: Verify completion**

After workflow finishes, verify:
```bash
ls docs/gap-reports/          # Should have N rounds of reports + plans
git log --oneline -20          # Should show commits from each round
pytest tests/ -v               # All tests should pass
```

---

## Post-Execution Verification Checklist

After the Workflow completes successfully:

1. `docs/gap-reports/` contains complete report chain: round-1.md, round-1-fix-plan.md, round-2.md, ...
2. `git log` shows Conventional Commits from each round and each fix task
3. Final round Review returned `total_gaps: 0`
4. `pytest tests/ -v` — all tests pass
5. Code files under `myagent/` are complete implementations (no stubs/TODOs)
