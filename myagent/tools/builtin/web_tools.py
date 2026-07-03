"""Built-in web tools: web_fetch, web_search."""

from __future__ import annotations

import html as _html_stdlib
import logging
import re
from urllib.parse import urlparse

from myagent.tools.base import ToolContext, ToolResult

logger = logging.getLogger("myagent.tools.web")

# ── HTML-to-text fallback (no markdownify) ──────────────────────────────────

_RE_SCRIPT_AND_STYLE = re.compile(
    r"<(script|style|noscript|iframe|svg|canvas|video|audio)\b[^>]*>.*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)
_RE_HEADINGS = re.compile(r"<(h[1-6])\b[^>]*>(.*?)</\1>", re.DOTALL | re.IGNORECASE)
_RE_PARAGRAPH = re.compile(r"<p\b[^>]*>(.*?)</p>", re.DOTALL | re.IGNORECASE)
_RE_LIST_ITEM = re.compile(r"<li\b[^>]*>(.*?)</li>", re.DOTALL | re.IGNORECASE)
_RE_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_WHITESPACE = re.compile(r"\n{3,}")


def _html_to_text(html_content: str) -> str:
    """Convert HTML to readable plain text (regex fallback)."""
    text = _RE_SCRIPT_AND_STYLE.sub("", html_content)

    # Replace heading/content elements with newline-wrapped text
    for pattern in (_RE_HEADINGS, _RE_PARAGRAPH, _RE_LIST_ITEM):
        text = pattern.sub(lambda m: "\n" + m.group(2).strip() + "\n", text)

    text = _RE_BR.sub("\n", text)
    text = _RE_TAG.sub("", text)
    text = _html_stdlib.unescape(text)
    text = _RE_WHITESPACE.sub("\n\n", text)
    return text.strip()


# ── Prompt-guided extraction ────────────────────────────────────────────────

def _extract_relevant(text: str, prompt: str, max_lines: int = 80) -> str:
    """Extract lines most relevant to the prompt (keyword overlap)."""
    if not prompt.strip():
        return text[:5000]

    keywords = set(re.findall(r"\w{3,}", prompt.lower()))
    lines = text.splitlines()
    scored = []
    for i, line in enumerate(lines):
        line_lower = line.lower()
        score = sum(1 for kw in keywords if kw in line_lower)
        if score:
            scored.append((score, i))

    if not scored:
        return text[:5000]

    scored.sort(key=lambda x: x[0], reverse=True)
    top_idxs = sorted({idx for _, idx in scored[:max_lines]})
    selected = [lines[i] for i in top_idxs]
    return "\n".join(selected)


# ── WebFetchTool ────────────────────────────────────────────────────────────

class WebFetchTool:
    name = "web_fetch"
    description = "Fetches a URL, converts the page to markdown, and answers a prompt against it."
    parameters = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "format": "uri",
                "description": "The URL to fetch content from",
            },
            "prompt": {
                "type": "string",
                "description": "The prompt to run on the fetched content",
            },
        },
        "required": ["url", "prompt"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        url = params["url"]
        prompt = params.get("prompt", "")
        logger.info(
            "web_fetch start: url=%s prompt_len=%d", url, len(prompt),
            extra={"category": "tool", "tool_name": "web_fetch"},
        )

        try:
            import httpx
        except ImportError:
            logger.error("web_fetch failed: httpx not available", extra={"category": "error", "component": "tool", "context": "web_fetch"})
            return ToolResult(error="httpx not available")

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                raw_html = response.text[:100000]
        except Exception as e:
            logger.error(
                "web_fetch HTTP error: url=%s error=%s", url, e,
                extra={"category": "error", "component": "tool", "context": "web_fetch"},
            )
            return ToolResult(error=f"Failed to fetch {url}: {e}")

        # Convert HTML to markdown/text
        markdown_content = None
        try:
            from markdownify import markdownify as md

            markdown_content = md(raw_html)
            logger.debug("web_fetch: used markdownify for HTML→markdown",
                         extra={"category": "tool"})
        except ImportError:
            logger.debug("web_fetch: markdownify not available, using regex fallback",
                         extra={"category": "tool"})
            markdown_content = _html_to_text(raw_html)

        # Apply prompt-guided extraction
        extracted = _extract_relevant(markdown_content, prompt)

        # Build output
        lines = []
        lines.append(f"Fetched from: {url}")
        lines.append(f"Status: {response.status_code}")
        lines.append(f"Content length: {len(raw_html)} chars (HTML), {len(markdown_content)} chars (markdown)")
        if prompt.strip():
            lines.append(f"Prompt: {prompt[:200]}")
        lines.append("")
        lines.append(extracted)

        output = "\n".join(lines)

        duration_ms = getattr(response, "elapsed", None)
        logger.info(
            "web_fetch done: url=%s status=%d html_len=%d md_len=%d extracted_len=%d",
            url, response.status_code, len(raw_html), len(markdown_content), len(extracted),
            extra={
                "category": "tool",
                "tool_name": "web_fetch",
                "duration_ms": int(duration_ms.total_seconds() * 1000) if duration_ms else None,
            },
        )

        return ToolResult(
            output=output,
            metadata={
                "url": url,
                "status_code": response.status_code,
                "html_length": len(raw_html),
                "markdown_length": len(markdown_content),
                "extracted_length": len(extracted),
                "prompt": prompt,
            },
        )


# ── WebSearchTool ───────────────────────────────────────────────────────────

_DDG_API = "https://api.duckduckgo.com/"


class WebSearchTool:
    name = "web_search"
    description = "Search the web. Returns result blocks with titles and URLs."
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "minLength": 2,
                "description": "The search query to use",
            },
            "allowed_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Only include results from these domains",
            },
            "blocked_domains": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Never include results from these domains",
            },
        },
        "required": ["query"],
    }

    async def execute(self, params: dict, context: ToolContext) -> ToolResult:
        query = params["query"]
        allowed_domains = params.get("allowed_domains", []) or []
        blocked_domains = params.get("blocked_domains", []) or []

        logger.info(
            "web_search start: query=%s", query,
            extra={"category": "tool", "tool_name": "web_search"},
        )

        try:
            import httpx
        except ImportError:
            logger.error("web_search failed: httpx not available", extra={"category": "error", "component": "tool", "context": "web_search"})
            return ToolResult(
                output=f"Web search for: {query}\n\n(httpx not available — cannot perform search)",
                metadata={"query": query},
            )

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    _DDG_API,
                    params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(
                "web_search API unavailable: query=%s error=%s", query, e,
                extra={"category": "tool", "tool_name": "web_search"},
            )
            # gap-8-11: Return a clear error result instead of a misleading stub.
            # The error field signals to the LLM that search failed, not that
            # there are no results. This prevents the model from treating a stub
            # as a successful search outcome.
            return ToolResult(
                error=(
                    f"Web search failed: DuckDuckGo API is currently unavailable. "
                    f"Query: '{query}'. Error: {str(e)[:200]}. "
                    f"Try again later or use web_fetch with a specific URL instead."
                ),
                metadata={"query": query, "search_error": True, "error_detail": str(e)[:300]},
            )

        results = self._parse_results(data, allowed_domains, blocked_domains)
        output = self._format_output(query, results)

        logger.info(
            "web_search done: query=%s result_count=%d",
            query, len(results),
            extra={"category": "tool", "tool_name": "web_search"},
        )

        return ToolResult(
            output=output,
            metadata={"query": query, "result_count": len(results), "source": "duckduckgo"},
        )

    def _parse_results(
        self,
        data: dict,
        allowed_domains: list[str],
        blocked_domains: list[str],
    ) -> list[dict]:
        """Extract results from DuckDuckGo Instant Answer API response."""
        entries: list[dict] = []

        # 1. Abstract (primary answer)
        abstract = data.get("AbstractText", "") or ""
        abstract_url = data.get("AbstractURL", "") or ""
        if abstract:
            entries.append({"title": data.get("Heading", "Result"), "url": abstract_url, "snippet": abstract})

        # 2. RelatedTopics
        for topic in data.get("RelatedTopics", []) or []:
            text = topic.get("Text", "") or ""
            url = topic.get("FirstURL", "") or ""
            if text and url:
                entries.append({"title": text.split(" - ")[0], "url": url, "snippet": text})

        # 3. Results (if present)
        for r in data.get("Results", []) or []:
            text = r.get("Text", "") or ""
            url = r.get("FirstURL", "") or ""
            if text and url:
                entries.append({"title": text.split(" - ")[0], "url": url, "snippet": text})

        # Deduplicate by URL
        seen = set()
        unique: list[dict] = []
        for e in entries:
            if e["url"] not in seen:
                seen.add(e["url"])
                unique.append(e)

        # Filter by allowed/blocked domains
        if allowed_domains:
            unique = [
                e for e in unique
                if any(domain in urlparse(e["url"]).netloc for domain in allowed_domains)
            ]
        if blocked_domains:
            unique = [
                e for e in unique
                if not any(domain in urlparse(e["url"]).netloc for domain in blocked_domains)
            ]

        return unique

    def _format_output(self, query: str, results: list[dict]) -> str:
        """Format results as markdown-style list."""
        if not results:
            return f"Search results for: {query}\n\nNo results found."

        lines = [f"Search results for: {query}", ""]
        for i, r in enumerate(results, 1):
            title = r["title"].strip()
            url = r["url"]
            snippet = r.get("snippet", "").strip()
            lines.append(f"{i}. **{title}**")
            lines.append(f"   {url}")
            if snippet:
                lines.append(f"   {snippet[:200]}")
            lines.append("")
        return "\n".join(lines).strip()
