# Task 8: Project Detection + Config Improvements

**Files:**
- Modify: `myagent/agent/project.py`
- Modify: `myagent/config/loader.py`

**Fixes audit issues:** #13 (no git root walk), #21 (no ~/env var expansion), #22 (AGENT.md levels inert)

## Global Constraints
- All fixes must pass `pytest tests/ -v` before commit
- No new dependencies
- Python 3.12+

## Steps

### Step 1: Project root auto-detection (audit #13)

Add `_find_git_root()` to `ProjectDetector`:
- Walk up directory tree from `start_dir`, looking for `.git` directory
- Max 10 levels up, stop at filesystem root
- Use found git root as `project_dir` in `detect()` if found

### Step 2: Config env var expansion (audit #21)

Add `_expand_env_vars()` to `ConfigLoader`:
- Expand `${VAR}` patterns in YAML content before parsing
- Use `os.environ` for values, leave unmatched patterns as-is
- Also expand `~` in path strings via `os.path.expanduser()`

### Step 3: AGENT.md frontmatter loading (audit #22)

Fix `_load_agent_md()`:
- Parse YAML frontmatter between `---` delimiters
- Map frontmatter keys to config sections: model, context, permissions, tools, ui
- Return config dict for merge

### Step 4: Run tests and commit

Run: `pytest tests/agent/test_project.py tests/config/ -v`
Expected: PASS

```bash
git add myagent/agent/project.py myagent/config/loader.py tests/
git commit -m "fix(config): project root auto-detection, env var expansion, AGENT.md frontmatter"
```
