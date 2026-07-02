# Task 8 Report: Project Detection + Config Improvements

**Status:** COMPLETE
**Commit:** `2e852c3`
**Date:** 2026-07-03
**Tests:** 213 passed, 0 failed

## Changes

### Step 1: Project root auto-detection (audit #13)

**File:** `myagent/agent/project.py`

Added `_find_git_root(start_dir)` static method to `ProjectDetector`:
- Walks up directory tree from `start_dir`, checking for `.git` directory
- Max 10 levels up; stops at filesystem root
- Returns `Path` to the directory containing `.git`, or `None`
- `detect()` now calls `_find_git_root()` and uses the discovered git root as `project_dir`

### Step 2: Config env var expansion (audit #21)

**File:** `myagent/config/loader.py`

Added `_expand_env_vars(content)` static method to `ConfigLoader`:
- `${VAR}` patterns are replaced with `os.environ` values; unmatched patterns left as-is
- `~` followed by `/` is expanded via `os.path.expanduser()`
- Called in `_load_yaml()` before `yaml.safe_load()`
- Also applied to AGENT.md frontmatter in `_load_agent_md()`

### Step 3: AGENT.md frontmatter loading (audit #22)

**File:** `myagent/config/loader.py`

Fixed `_load_agent_md()` to parse YAML frontmatter:
- Detects `---` delimiter at file start
- Extracts YAML content between opening and closing `---`
- Parses via `yaml.safe_load()` with env var expansion applied
- Maps recognized keys to config sections: `model`, `context`, `permissions`, `tools`, `ui`, `subagents`, `dream`, `session`, `logging`
- Unknown keys are silently ignored
- Returns config dict ready for merge into the 7-level priority chain

### New Tests (13 added)

**`tests/agent/test_project.py`** (4 new):
- `test_find_git_root_current_dir` -- .git found in given directory
- `test_find_git_root_parent_dir` -- walks up to parent with .git
- `test_find_git_root_not_found` -- returns None when no .git anywhere
- `test_find_git_root_max_depth` -- stops after 10 levels

**`tests/config/test_loader.py`** (9 new):
- `test_expand_env_vars_in_config` -- `${VAR}` expands from environment
- `test_expand_env_vars_unmatched_left_as_is` -- unmatched vars preserved
- `test_expand_tilde_in_config` -- `~/path` expands to home directory
- `test_agent_md_yaml_frontmatter` -- frontmatter parsed and merged
- `test_agent_md_no_frontmatter_graceful` -- no frontmatter returns defaults
- `test_agent_md_unknown_keys_ignored` -- unrecognized keys dropped
- `test_agent_md_multiple_sections` -- multiple config sections set
- `test_agent_md_expand_env_in_frontmatter` -- `${VAR}` expanded in frontmatter
- `test_project_agent_md_level4` -- project AGENT.md overrides user config

## Test Summary

```
======================== 213 passed, 3 warnings in 24.25s ========================
```
