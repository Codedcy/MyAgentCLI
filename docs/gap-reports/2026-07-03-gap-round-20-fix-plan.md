---
date: 2026-07-03
round: 20
gaps_to_fix: 9
source_report: D:\code\myagentcli\docs\gap-reports\2026-07-03-gap-round-20.md
---

# Fix Plan — Round 20

## Summary
Fix 9 gaps across 7 files. Two medium-severity gaps (dream rounds counting, sub-agent context compression) and seven low-severity corrections.

## Task 1: Dream engine rounds-since-last-dream tracking
- **Gap IDs**: gap-20-08
- **Files**: myagent/memory/dream.py, myagent/agent/session.py
- **Approach**: The dream engine currently feeds `estimate_total_rounds()` total historical session rounds into `should_run()`, making the 50-round threshold trivially satisfied. Fix: `estimate_total_rounds()` accepts an optional `since_timestamp` parameter; only sessions with `created_at` after that timestamp are counted. `DreamEngine.should_run()` passes `last_run` from last_dream.json as the cutoff. This ensures the 50-round threshold counts rounds accumulated SINCE the last dream, matching the spec: "累计对话轮数 > 50 轮" in the context of triggering.
- **Verification**: Check that `should_run()` uses the filtered round count. Run `pytest tests/memory/ -v`.

## Task 2: Sub-agent worker context compression
- **Gap IDs**: gap-20-01
- **Files**: myagent/subagent/worker.py
- **Approach**: Add a lightweight context-size guard in `SubAgentWorker._run_impl()`. After each iteration, estimate total message character count. When it exceeds 750KB (~75% of 1M token context at ~4 chars/token), apply truncation: keep system prompt, first 2 user/assistant messages, and last 10 messages. Log the compression event. The 90% hard limit (900KB) triggers more aggressive truncation with a warning injected into the message stream.
- **Verification**: Verify the worker has compression logic. Run `pytest tests/subagent/ -v`.

## Task 3: LLM retry backoff exact spec parameters
- **Gap IDs**: gap-20-02
- **Files**: myagent/llm/provider.py
- **Approach**: Replace litellm's built-in retry mechanism with our own retry loop in `_complete_with_model`. Set `litellm.num_retries = 0` to disable internal retries. Implement exponential backoff in `_complete_with_model`: base 2s, max 30s, 3 retries (exact spec values). Wrap the litellm.acompletion call in a retry loop that catches retryable LLMErrors and applies the exact backoff formula.
- **Verification**: Verify retry constants match spec (2s base, 30s max, 3 retries). Run `pytest tests/llm/ -v`.

## Task 4: Skill resource relative paths
- **Gap IDs**: gap-20-03
- **Files**: myagent/skills/loader.py, myagent/context/builder.py
- **Approach**: In `SkillLoader.enumerate_resources()`, convert absolute Path objects to relative paths (relative to `skill_dir`). In `ContextBuilder._format_skill_content()`, ensure resources are displayed as relative paths (e.g., `scripts/lint.sh` not `/abs/path/scripts/lint.sh`).
- **Verification**: Check resource paths are relative. Run `pytest tests/skills/ -v`.

## Task 5: CLI help text for --session --export
- **Gap IDs**: gap-20-04
- **Files**: myagent/cli/main.py
- **Approach**: Add an `epilog` to the argparse ArgumentParser documenting the compound usage `myagent --session <id> --export markdown`. Add usage examples section.
- **Verification**: Check argparse help output includes export examples.

## Task 6: Skill invocation metrics tracking
- **Gap IDs**: gap-20-05
- **Files**: myagent/agent/engine.py, myagent/cli/commands.py
- **Approach**: Add logging in two places: (1) In `AgentEngine._react_loop()` when `skill_invoke` tool call is intercepted, log with `category="skill"`, `event="invoked"`, skill name, and invocation source (model vs. slash command). (2) In `CommandDispatcher.dispatch()` when a `/skill-name` is dispatched, log the skill invocation.
- **Verification**: Verify skill invocations are logged with proper category. Check `pytest tests/agent/ -v`.

## Task 7: SendMessageTool "from" field in schema
- **Gap IDs**: gap-20-06
- **Files**: myagent/tools/builtin/agent_tools.py
- **Approach**: Add `from` field to the SendMessageTool parameter schema with description: "Sender agent ID (populated automatically in sub-agent context)". In `execute()`, if `from` is not provided and the ToolContext indicates sub-agent context, generate a default sender ID. This makes the contract explicit rather than relying on sub-agents discovering a non-schema parameter.
- **Verification**: Verify schema includes `from` field. Run `pytest tests/tools/ -v`.

## Task 8: PermissionController non-TTY safety
- **Gap IDs**: gap-20-07
- **Files**: myagent/permissions/controller.py
- **Approach**: Change the non-TTY fallback in `PermissionController.confirm()` from `return True` (auto-allow) to `return False` (deny). This aligns with the spec: "权限确认不设超时——一直等待用户明确响应". When TTY is unavailable, we cannot get user confirmation, so we must deny. Add a clear error message explaining why. Add a `--dangerously-skip-permissions` check to allow bypass in CI (since that flag already means full trust).
- **Verification**: Confirm non-TTY returns False. Run `pytest tests/permissions/ -v`.

## Task 9: ContextBuilder memory cache hash-based key
- **Gap IDs**: gap-20-09
- **Files**: myagent/context/builder.py
- **Approach**: Replace `_cache_key = query[:100]` with a content-based hash. Extract significant keywords from the query using the existing `_tokenize_for_cache()`, sort them, join, and compute a SHA256 hash prefix. This avoids false cache hits on long inputs sharing a common prefix. The `_detect_topic_drift` method already provides a semantic safety net; the hash key provides a structural one.
- **Verification**: Verify cache key derivation is hash-based. Run `pytest tests/context/ -v`.
