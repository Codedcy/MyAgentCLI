"""Built-in web tools: web_fetch, web_search."""

from __future__ import annotations

from myagent.tools.base import ToolContext, ToolResult


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

        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(url)
                response.raise_for_status()
                content = response.text[:100000]  # Truncate to 100KB
                return ToolResult(
                    output=f"Fetched {len(content)} chars from {url}\nStatus: {response.status_code}\n\n{content[:5000]}",
                    metadata={
                        "url": url,
                        "status_code": response.status_code,
                        "content_length": len(content),
                        "prompt": prompt,
                    },
                )
        except ImportError:
            return ToolResult(error="httpx not available")
        except Exception as e:
            return ToolResult(error=f"Failed to fetch {url}: {e}")


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
        return ToolResult(
            output=f"Web search for: {query}\n\nResults: (web search requires API key configuration — results not available)",
            metadata={"query": query},
        )
