# Task 10: Memory, Logging, Session, Context, Skills, LLM, Commands — Spec Alignment

**Files:**
- Modify: `myagent/memory/store.py` (dedup, [[link]])
- Modify: `myagent/logging/logger.py` (size rotation)
- Modify: `myagent/logging/formatter.py` (missing fields: pid, traceback, component, context)
- Modify: `myagent/context/persistence.py` (load messages, full transcript)
- Modify: `myagent/context/builder.py` (L5 skills, L6 goal context)
- Modify: `myagent/context/compression.py` (LLM summarization in Layer 3)
- Modify: `myagent/skills/registry.py` (recursive directory scan)
- Modify: `myagent/tools/builtin/exec_tools.py` (sandbox enforcement)
- Modify: `myagent/tools/builtin/memory_tools.py` (MEMORY.md index)
- Modify: `myagent/tools/builtin/session_tools.py` (persist across sessions)
- Modify: `myagent/cli/commands.py` (/exit confirm, /clear real, /history real)
- Modify: `myagent/llm/provider.py` (logging, fallback models)

**Fixes audit issues:** #14, #15, #20, #23, #24, #25, #26, #27, #28, #29, #30, #32, #33, #34, #35, #41, #44, #45

## Global Constraints
- All fixes must pass `pytest tests/ -v` before commit
- No new dependencies (markdownify optional)
- All modules use `logging.getLogger("myagent.<module>")`
- Python 3.12+

## Steps Summary

1. **Memory dedup + links (audit #29, #30):** write() checks existing files by name; extract [[links]]
2. **Logging size rotation (audit #15):** Add RotatingFileHandler with max_size_mb
3. **Logging formatter fields (audit #41):** Add pid, traceback, component, context to JsonLineFormatter
4. **Session persistence (audit #23, #28):** load_session restores messages; save ALL messages not just last 50
5. **Context builder (audit #26, #27):** L5 inject skill content on invoke; L6 inject goal context
6. **Compression Layer 3 (audit #24):** Real LLM call for conversation summary instead of placeholder
7. **Skills recursive scan (audit #33):** Scan nested subdirectories
8. **Exec sandbox (audit #20):** Check dangerouslyDisableSandbox, enforce via permissions
9. **LLM provider (audit #14, #45):** Add request/response logging; implement fallback model support
10. **Commands (audit #44):** /exit confirm prompt; /clear clears messages; /history shows real history

### Run tests and commit
```bash
pytest tests/ -v
git add myagent/
git commit -m "fix: spec alignment — memory, logging, session, context, skills, sandbox, LLM, commands"
```
