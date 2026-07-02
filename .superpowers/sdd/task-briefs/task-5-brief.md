# Task 5: Fix Web Tools — real web_search + HTML→Markdown in web_fetch

**Files:**
- Modify: `myagent/tools/builtin/web_tools.py`

**Fixes audit issues:** #3 (web_search stub), #19 (web_fetch no HTML→Markdown)

## Global Constraints
- All fixes must pass `pytest tests/ -v` before commit
- No new dependencies (httpx already listed; markdownify is optional)
- Use `logging.getLogger("myagent.tools.web")` for logging
- Python 3.12+

## Steps

### Step 1: Fix web_fetch — HTML to Markdown conversion + prompt usage

Current: Returns raw HTML truncated to 5000 chars. `prompt` parameter unused.

Fix:
1. After fetching HTML, convert to Markdown:
   - Try `markdownify` library (optional, graceful fallback)
   - Fallback: regex-based HTML→text (strip scripts/styles, convert h1-h6/p/li/br, remove tags, decode entities)
2. If prompt is provided, extract relevant lines matching prompt keywords
3. Return content summary with metadata
4. Add proper logging

### Step 2: Fix web_search — real search via DuckDuckGo API

Current: Returns hardcoded "requires API key configuration" placeholder.

Fix:
1. Use DuckDuckGo Instant Answer API (no API key needed): `https://api.duckduckgo.com/?q=...&format=json`
2. Parse response: AbstractText, RelatedTopics, Results
3. Format as markdown list with titles and URLs
4. Handle no-results case gracefully
5. Add proper logging
6. Fall back to stub message if httpx unavailable

### Step 3: Run tests and commit

Run: `pytest tests/tools/builtin/test_web_tools.py -v`
Expected: PASS

```bash
git add myagent/tools/builtin/web_tools.py tests/tools/builtin/test_web_tools.py
git commit -m "fix(web): real web_search via DuckDuckGo; HTML→Markdown in web_fetch"
```
