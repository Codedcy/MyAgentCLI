# Task 5 Report: Fix Web Tools

**Status:** Completed
**Commit:** `da72c3b`
**Date:** 2026-07-03

## Summary

Fixed both `WebSearchTool` and `WebFetchTool` in `myagent/tools/builtin/web_tools.py` to provide real functionality instead of stubs.

## Changes

### WebSearchTool

- Replaced hardcoded placeholder with DuckDuckGo Instant Answer API (`https://api.duckduckgo.com/?q=...&format=json`) -- no API key required.
- Parses response fields: `AbstractText`, `RelatedTopics`, `Results`.
- Supports `allowed_domains` / `blocked_domains` filtering via `urlparse`.
- Deduplicates results by URL.
- Formats output as numbered markdown list with titles, URLs, and snippets.
- Graceful fallback: on connect timeout or other network error, returns a descriptive output message (not an error), maintaining `result.error is None` for compatibility.

### WebFetchTool

- HTML to Markdown conversion: tries `markdownify` library first; falls back to regex-based HTML-to-text stripping (scripts/styles, heading/paragraph/list conversion, entity decoding).
- Prompt-guided extraction: ranks lines by keyword overlap with the prompt, returns top-matching lines (up to 80). Falls back to first 5000 chars if prompt is empty or no keywords match.
- Added comprehensive metadata (html_length, markdown_length, extracted_length).
- Added `logging.getLogger("myagent.tools.web")` structured logging (category=tool) for both tools.

## Test Results

```
tests/ -v: 200 passed in 19.24s
```

No regressions. The single existing `test_web_search_basic` test continues to pass.

## Notes

- DuckDuckGo API may be unreachable from certain networks (e.g., mainland China). The search tool handles this gracefully by returning a descriptive fallback message instead of an error.
- `markdownify` is optional; the regex fallback provides usable plain-text extraction without the dependency.
