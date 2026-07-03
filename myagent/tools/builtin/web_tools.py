"""Built-in web tools: web_fetch, web_search."""

from __future__ import annotations

import html as _html_stdlib
import logging
import re
from urllib.parse import urlparse

from myagent.tools.base import ToolContext, ToolResult

logger = logging.getLogger("myagent.tools.web")

# Model to use for prompt-guided answering in web_fetch.
# Per spec §四 工具系统, web_fetch should "answer `prompt` against it
# using a small fast model". We use the configured web_fetch_answer_model
# (default: deepseek/deepseek-chat) in Non-think mode, NOT the primary
# model (DeepSeek V4 Pro). Falls back to regex extraction if the LLM
# call fails.
_WEB_FETCH_ANSWER_MODEL_MODE = "Non-think"
# Default model for web_fetch Q&A — a small, fast model for lightweight
# content extraction. Overridden by config.tools.web_fetch_answer_model.
_WEB_FETCH_DEFAULT_ANSWER_MODEL = "deepseek/deepseek-chat"
# Maximum characters of fetched content to send to the LLM for answering.
# The model needs enough context to answer the question, but we cap it
# to keep latency low and avoid token waste.
_WEB_FETCH_MAX_CONTENT_CHARS = 15000
# Maximum characters to return from the LLM answer.
_WEB_FETCH_MAX_ANSWER_CHARS = 8000

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

    # Replace heading/content elements with newline-wrapped text.
    # Group count varies by pattern: h1-h6 and p have 2 groups (tag name + content),
    # li has 1 group (content only). Use last group for content extraction.
    for pattern in (_RE_HEADINGS, _RE_PARAGRAPH, _RE_LIST_ITEM):
        text = pattern.sub(lambda m: "\n" + m.group(m.re.groups).strip() + "\n", text)

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
            logger.error(
                "web_fetch failed: httpx not available",
                exc_info=True,
                extra={"category": "error", "component": "tool", "context": "web_fetch"},
            )
            return ToolResult(error="httpx not available")

        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                raw_html = response.text[:100000]
        except Exception as e:
            logger.error(
                "web_fetch HTTP error: url=%s error=%s", url, e,
                exc_info=True,
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

        # ── Prompt-guided answering ──
        # Primary: use a lightweight LLM call to answer the prompt against content.
        # Fallback: keyword-based regex extraction (_extract_relevant).
        answer_text = ""
        llm_used = False
        if prompt.strip():
            answer_text, llm_used = await self._llm_answer(prompt, markdown_content, context)

        if not answer_text:
            # LLM call failed or prompt was empty — fall back to regex extraction
            logger.debug("web_fetch: falling back to regex extraction",
                         extra={"category": "tool"})
            answer_text = _extract_relevant(markdown_content, prompt)

        # Build output
        lines = []
        lines.append(f"Fetched from: {url}")
        lines.append(f"Status: {response.status_code}")
        lines.append(
            f"Content length: {len(raw_html)} chars (HTML), "
            f"{len(markdown_content)} chars (markdown)"
        )
        if prompt.strip():
            lines.append(f"Prompt: {prompt[:200]}")
        if llm_used:
            lines.append(f"Answer method: LLM ({_WEB_FETCH_ANSWER_MODEL_MODE} mode)")
        else:
            lines.append("Answer method: keyword extraction (LLM unavailable)")
        lines.append("")
        lines.append(answer_text)

        output = "\n".join(lines)

        duration_ms = getattr(response, "elapsed", None)
        logger.info(
            "web_fetch done: url=%s status=%d html_len=%d md_len=%d answer_len=%d llm_used=%s",
            url, response.status_code, len(raw_html), len(markdown_content),
            len(answer_text), llm_used,
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
                "answer_length": len(answer_text),
                "prompt": prompt,
                "llm_used": llm_used,
            },
        )

    @staticmethod
    async def _llm_answer(prompt: str, content: str, context: ToolContext) -> tuple[str, bool]:
        """Use a lightweight LLM call to answer the prompt against the content.

        Returns (answer_text, llm_used). If the LLM call fails for any reason,
        returns ("", False) so the caller can fall back to regex extraction.

        Per spec §四 工具系统 — web_fetch: the tool should "fetch a URL, convert
        the page to markdown, and answer `prompt` against it using a small fast
        model". We use the configured model in Non-think mode without tools to
        get a fast, focused answer.
        """
        try:
            import litellm  # noqa: F811
        except ImportError:
            logger.debug("web_fetch: litellm not available for LLM answering",
                         extra={"category": "tool"})
            return "", False

        # Determine the answer model from config.  Per spec §四 工具系统,
        # web_fetch should use a "small fast model", NOT the primary model.
        # Priority: config.tools.web_fetch_answer_model > constant default.
        model_config = getattr(context, "config", None)
        if model_config is not None:
            tools_cfg = getattr(model_config, "tools", None)
            if tools_cfg is not None:
                answer_model = getattr(
                    tools_cfg, "web_fetch_answer_model", _WEB_FETCH_DEFAULT_ANSWER_MODEL
                )
            else:
                answer_model = _WEB_FETCH_DEFAULT_ANSWER_MODEL
        else:
            answer_model = _WEB_FETCH_DEFAULT_ANSWER_MODEL

        # Truncate content to keep the prompt within reasonable token limits
        truncated = content[:_WEB_FETCH_MAX_CONTENT_CHARS]
        if len(content) > _WEB_FETCH_MAX_CONTENT_CHARS:
            truncated += "\n\n[Content truncated ...]"

        system_message = (
            "You are a helpful assistant that answers questions based on web page content. "
            "Answer the user's question using ONLY the provided web page content. "
            "If the content does not contain enough information to answer the question, "
            "say so clearly. Be concise and direct."
        )

        user_message = (
            f"Based on the following web page content, answer this question: {prompt}\n\n"
            f"--- WEB PAGE CONTENT ---\n{truncated}\n--- END CONTENT ---\n\n"
            f"Please answer the question: {prompt}"
        )

        messages = [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ]

        try:
            response = await litellm.acompletion(
                model=answer_model,
                messages=messages,
                stream=False,
                max_tokens=min(_WEB_FETCH_MAX_ANSWER_CHARS // 2, 4096),
            )

            if response and response.choices:
                answer = response.choices[0].message.content or ""
                answer = answer[:_WEB_FETCH_MAX_ANSWER_CHARS]
                logger.info(
                    "web_fetch: LLM answer obtained, length=%d model=%s",
                    len(answer), answer_model,
                    extra={"category": "tool", "tool_name": "web_fetch"},
                )
                return answer, True

            return "", False

        except Exception as e:
            logger.error(
                "web_fetch: LLM answering failed (%s), falling back to regex extraction",
                e,
                exc_info=True,
                extra={
                    "category": "error",
                    "component": "tool",
                    "context": "web_fetch.llm_answer",
                    "tool_name": "web_fetch",
                },
            )
            return "", False


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
            logger.error(
                "web_search failed: httpx not available",
                exc_info=True,
                extra={"category": "error", "component": "tool", "context": "web_search"},
            )
            return ToolResult(
                output=f"Web search for: {query}\n\n(httpx not available — cannot perform search)",
                metadata={"query": query},
            )

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    _DDG_API,
                    params={"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"},
                    headers={"User-Agent": "MyAgentCLI/0.1"},
                )
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "text/html" in content_type:
                    logger.warning(
                        "web_search: DuckDuckGo returned HTML instead of JSON (status=%d)",
                        resp.status_code,
                        extra={"category": "tool", "tool_name": "web_search"},
                    )
                    return ToolResult(
                        error=(
                            f"Web search failed: DuckDuckGo API returned HTML page "
                            f"(HTTP {resp.status_code}) instead of JSON. "
                            f"The API may be blocking automated requests. "
                            f"Try web_fetch with a specific search-engine URL instead."
                        ),
                        metadata={"query": query, "search_error": True},
                    )
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                "web_search HTTP error: query=%s status=%d error=%s",
                query, e.response.status_code, e,
                exc_info=True,
                extra={
                    "category": "error",
                    "component": "tool",
                    "context": "web_search.http_status",
                    "tool_name": "web_search",
                },
            )
            return ToolResult(
                error=(
                    f"Web search failed: DuckDuckGo API returned HTTP "
                    f"{e.response.status_code}. Query: '{query}'. "
                    f"Try again later or use web_fetch with a specific URL instead."
                ),
                metadata={
                    "query": query,
                    "search_error": True,
                    "http_status": e.response.status_code,
                },
            )
        except Exception as e:
            logger.error(
                "web_search API unavailable: query=%s error=%s type=%s",
                query, e, type(e).__name__,
                exc_info=True,
                extra={
                    "category": "error",
                    "component": "tool",
                    "context": "web_search.api",
                    "tool_name": "web_search",
                },
            )
            return ToolResult(
                error=(
                    f"Web search failed: DuckDuckGo API is currently unavailable. "
                    f"Query: '{query}'. Reason: {type(e).__name__}: {str(e)[:200]}. "
                    f"Try again later or use web_fetch with a specific URL instead."
                ),
                metadata={
                    "query": query,
                    "search_error": True,
                    "error_detail": f"{type(e).__name__}: {str(e)[:300]}",
                },
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
            entries.append({
                "title": data.get("Heading", "Result"),
                "url": abstract_url,
                "snippet": abstract,
            })

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
