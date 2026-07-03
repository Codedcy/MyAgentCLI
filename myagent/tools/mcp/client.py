"""MCP subprocess client — JSON-RPC over pluggable transport.

Manages lifecycle of one MCP server:
initialize → tools/list → (runtime calls) → shutdown

Transport abstraction allows swapping stdio for SSE/HTTP in the future.

Design doc reference: §四 工具系统 — MCP 集成
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

logger = logging.getLogger("myagent.tools.mcp")

if TYPE_CHECKING:
    import httpx


# ── Transport abstraction (gap-34) ──────────────────────────────


class MCPTransport(Protocol):
    """Pluggable transport for MCP JSON-RPC communication.

    Implementations:
      - StdioTransport: subprocess stdin/stdout
      - SSE (future): HTTP long-polling
    """

    async def connect(self) -> None:
        """Establish the transport connection."""
        ...

    async def send(self, data: bytes) -> None:
        """Send data over the transport."""
        ...

    async def receive(self) -> bytes | None:
        """Receive data from the transport (blocks until data or EOF)."""
        ...

    async def close(self) -> None:
        """Close the transport and release resources."""
        ...


class StdioTransport:
    """MCP transport over subprocess stdin/stdout.

    Spawns a child process and communicates via its stdin/stdout pipes.
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ):
        self.command = command
        self.args = args or []
        self.env = env or {}
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_task: asyncio.Task | None = None

    async def connect(self) -> None:
        env = {**__import__("os").environ, **self.env}
        self._process = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        # Start stderr drainer
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def send(self, data: bytes) -> None:
        if not self._process or self._process.stdin is None:
            raise RuntimeError("Transport not connected")
        self._process.stdin.write(data)
        await self._process.stdin.drain()

    async def receive(self) -> bytes | None:
        if not self._process or self._process.stdout is None:
            return None
        return await self._process.stdout.read(4096)

    async def close(self) -> None:
        if self._stderr_task:
            self._stderr_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stderr_task
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                logger.exception(
                    "Timed out while terminating MCP process",
                    extra={
                        "category": "error",
                        "component": "mcp",
                        "context": "terminate mcp process",
                    },
                )
                self._process.kill()
                await self._process.wait()
            except ProcessLookupError:
                logger.exception(
                    "MCP process disappeared during shutdown",
                    extra={
                        "category": "error",
                        "component": "mcp",
                        "context": "shutdown mcp process",
                    },
                )
                pass

    async def _drain_stderr(self) -> None:
        """Read stderr line-by-line to prevent pipe buffer deadlock.

        Each line is classified for severity: error/critical lines logged
        at ERROR, warnings at WARNING, everything else at DEBUG. This
        ensures MCP server crashes and diagnostics are visible at default
        INFO log level (gap-10-4).
        """
        if not self._process or self._process.stderr is None:
            return
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                # Classify severity
                line_lower = decoded.lower()
                if any(marker in line_lower for marker in (
                    "error", "traceback", "panic", "fatal", "critical",
                    "exception", "fail",
                )):
                    logger.error(
                        "MCP stderr [%s]: %s",
                        self.command,
                        decoded,
                        extra={
                            "category": "error",
                            "component": "mcp",
                            "context": "mcp_stderr",
                        },
                    )
                elif any(marker in line_lower for marker in ("warn",)):
                    logger.warning("MCP stderr [%s]: %s", self.command, decoded,
                                   extra={"category": "system"})
                else:
                    logger.debug(
                        "MCP stderr [%s]: %s",
                        self.command,
                        decoded,
                        extra={"category": "system"},
                    )
        except asyncio.CancelledError:
            logger.exception(
                "MCP stderr drainer cancelled",
                extra={
                    "category": "error",
                    "component": "mcp",
                    "context": "drain mcp stderr",
                },
            )
            pass
        except Exception:
            logger.exception(
                "MCP stderr drainer stopped for %s", self.command, exc_info=True,
                extra={
                    "category": "error",
                    "component": "mcp",
                    "context": "mcp.stderr_drain",
                },
            )


class SSETransport:
    """MCP transport over HTTP SSE (Server-Sent Events) (gap-19-05).

    Connects to an MCP server via:
    - GET  /sse       — SSE stream for server->client messages
    - POST /message   — HTTP POST for client->server messages

    Per MCP transport spec: the client opens an SSE stream and sends
    JSON-RPC messages via POST. Responses are delivered as SSE events.

    Uses a single httpx.AsyncClient for the entire connection lifetime.
    SSE parsing uses a proper state machine that handles comments,
    multi-line data fields, and event types correctly.
    """

    # Regex for SSE field lines: "field: value" or "field:value"
    _SSE_LINE_RE = re.compile(r"^(?P<field>[^:\r\n]+):\s?(?P<value>.*)$")

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        session_id: str | None = None,
    ):
        self.url = url.rstrip("/")
        self.headers = headers or {}
        self.session_id = session_id
        self._client: httpx.AsyncClient | None = None
        self._sse_response: httpx.Response | None = None
        self._receive_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._connected = False
        self._reader_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """Open SSE stream via a single httpx.AsyncClient.

        Per MCP SSE spec: GET /sse starts the SSE stream. The server
        responds with an 'endpoint' event containing the POST URL for
        client->server messages. A session ID query param may be used
        to reconnect.

        gap-19-05: Previously created three httpx.AsyncClient instances
        and immediately destroyed the first two. Now creates exactly one
        client and one stream for the entire connection lifetime.
        """
        import httpx

        # Build SSE URL with optional session_id
        sse_url = f"{self.url}/sse"
        if self.session_id:
            sse_url = f"{sse_url}?sessionId={self.session_id}"

        # Create a single HTTP client for the entire connection lifetime
        self._client = httpx.AsyncClient(
            headers={
                "Accept": "text/event-stream",
                "Cache-Control": "no-cache",
                **self.headers,
            },
            timeout=httpx.Timeout(60.0, connect=15.0),
        )

        # Open SSE stream via a streaming GET request
        self._sse_response = await self._client.send(
            httpx.Request("GET", sse_url),
            stream=True,
        )
        self._connected = True

        # Start background reader that parses SSE events and enqueues
        # complete JSON-RPC messages to _receive_queue.
        self._reader_task = asyncio.create_task(self._sse_reader())

    async def send(self, data: bytes) -> None:
        """Send a JSON-RPC message via POST to the message endpoint."""
        if not self._client:
            raise RuntimeError("SSE transport not connected")

        post_url = f"{self.url}/message"
        if self.session_id:
            post_url = f"{post_url}?sessionId={self.session_id}"

        response = await self._client.post(
            post_url,
            content=data,
            headers={"Content-Type": "application/json"},
        )
        if response.status_code not in (200, 202):
            logger.warning(
                "SSE POST returned %d: %s",
                response.status_code,
                response.text[:200],
                extra={
                    "category": "error",
                    "component": "mcp",
                    "context": "sse.post_status",
                },
            )

    async def receive(self) -> bytes | None:
        """Receive a JSON-RPC message from the SSE stream.

        Blocks until a complete message is available or returns None
        if the stream ended.
        """
        if not self._connected:
            return None
        try:
            return await asyncio.wait_for(self._receive_queue.get(), timeout=30.0)
        except TimeoutError:
            logger.exception(
                "Timed out waiting for SSE message",
                extra={
                    "category": "error",
                    "component": "mcp",
                    "context": "receive SSE message",
                },
            )
            return None

    async def close(self) -> None:
        """Close the SSE transport and release resources."""
        self._connected = False

        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task

        if self._sse_response:
            try:
                await self._sse_response.aclose()
            except Exception:
                logger.exception(
                    "Failed to close SSE response",
                    extra={
                        "category": "error",
                        "component": "mcp",
                        "context": "sse.close_response",
                    },
                )

        if self._client:
            try:
                await self._client.aclose()
            except Exception:
                logger.exception(
                    "Failed to close SSE client",
                    extra={
                        "category": "error",
                        "component": "mcp",
                        "context": "sse.close_client",
                    },
                )

        # Drain the receive queue
        while not self._receive_queue.empty():
            try:
                self._receive_queue.get_nowait()
            except asyncio.QueueEmpty:
                logger.exception(
                    "SSE receive queue drained",
                    extra={
                        "category": "error",
                        "component": "mcp",
                        "context": "drain SSE receive queue",
                    },
                )
                break

    async def _sse_reader(self) -> None:
        """Read raw bytes from the SSE stream and parse SSE events.

        SSE format per W3C spec:
            event: <event-type>    (optional)
            id: <event-id>         (optional)
            data: <payload>        (one or more lines)
            : <comment>            (ignored)
            <blank line>           (event boundary)

        Complete events (terminated by double newline) are parsed and
        enqueued as MCP-framed messages to _receive_queue.

        gap-19-05: Uses a proper state-machine approach that handles:
        - Multi-line data fields (multiple data: lines joined by \\n)
        - Comments (lines starting with :)
        - Event type and id fields
        - All line ending styles (\\n, \\r\\n)
        """
        try:
            buffer = b""
            while self._connected:
                try:
                    # Read raw bytes from the stream
                    if hasattr(self._sse_response, 'aiter_bytes'):
                        chunk = await asyncio.wait_for(
                            self._sse_response.aiter_bytes().__anext__(),
                            timeout=5.0,
                        )
                    elif hasattr(self._sse_response, 'aiter_raw'):
                        chunk = await asyncio.wait_for(
                            self._sse_response.aiter_raw().__anext__(),
                            timeout=5.0,
                        )
                    else:
                        logger.error(
                            "SSE stream has no aiter_bytes or aiter_raw",
                            extra={
                                "category": "error",
                                "component": "mcp",
                                "context": "sse.reader_missing_stream",
                            },
                        )
                        break

                    if not chunk:
                        break
                    buffer += chunk

                    # Process complete SSE events (separated by \\n\\n or \\r\\n\\r\\n)
                    while True:
                        # Find the next event boundary (double newline)
                        # Normalize: treat \r\n as \n for boundary detection
                        boundary = buffer.find(b"\n\n")
                        if boundary == -1:
                            # Also try \r\n\r\n (Windows-style)
                            boundary = buffer.find(b"\r\n\r\n")
                            if boundary == -1:
                                break
                            # Found \r\n\r\n boundary
                            event_bytes = buffer[:boundary]
                            buffer = buffer[boundary + 4:]
                        else:
                            event_bytes = buffer[:boundary]
                            buffer = buffer[boundary + 2:]

                        # Parse and enqueue the event
                        message = self._parse_sse_event(event_bytes)
                        if message:
                            await self._receive_queue.put(message)

                except TimeoutError:
                    logger.exception(
                        "SSE reader timed out waiting for bytes",
                        extra={
                            "category": "error",
                            "component": "mcp",
                            "context": "read SSE bytes",
                        },
                    )
                    continue
                except StopAsyncIteration:
                    logger.exception(
                        "SSE reader stream ended",
                        extra={
                            "category": "error",
                            "component": "mcp",
                            "context": "read SSE stream",
                        },
                    )
                    break
                except Exception:
                    logger.exception(
                        "SSE reader error",
                        extra={
                            "category": "error",
                            "component": "mcp",
                            "context": "sse.reader",
                        },
                    )
                    break
        except asyncio.CancelledError:
            logger.exception(
                "SSE reader task cancelled",
                extra={
                    "category": "error",
                    "component": "mcp",
                    "context": "run SSE reader",
                },
            )
            pass

    def _parse_sse_event(self, event_bytes: bytes) -> bytes | None:
        """Parse a single SSE event into an MCP-framed message.

        Handles the full SSE spec (gap-19-05):
        - event: field — event type
        - id: field — event ID
        - data: field — payload (multiple data: lines joined by \\n)
        - : comment — ignored (line starts with colon)
        - Empty lines within an event are part of the data field

        Returns None for comment-only events (no data field) or parse errors.
        Returns MCP Content-Length framed bytes on success.
        """
        text = event_bytes.decode("utf-8", errors="replace")
        data_lines: list[str] = []

        for line in text.split("\n"):
            line = line.rstrip("\r")

            # Skip empty lines (should not appear within a single event,
            # but be safe)
            if not line:
                continue

            # Comment line (starts with colon) — skip per SSE spec
            if line.startswith(":"):
                continue

            match = self._SSE_LINE_RE.match(line)
            if not match:
                # Line without a colon is invalid per SSE spec — skip
                continue

            field = match.group("field").strip()
            value = match.group("value") or ""

            if field == "data":
                data_lines.append(value)
            elif field == "event":
                pass
            elif field == "id":
                # Store event ID for potential reconnection, ignored for now
                pass
            # Other fields (retry, etc.) are ignored

        # No data lines — this is a comment-only event or heartbeat
        if not data_lines:
            return None

        # Join multiple data lines with newline (SSE spec: data is the
        # concatenation of data field values, separated by \\n if multiple)
        data = "\n".join(data_lines)

        try:
            # Wrap in MCP framing (Content-Length header + body)
            body = data.encode("utf-8")
            header = f"Content-Length: {len(body)}\r\n\r\n"
            return header.encode("utf-8") + body
        except Exception:
            logger.exception(
                "Failed to encode SSE event",
                extra={
                    "category": "error",
                    "component": "mcp",
                    "context": "sse.parse_event",
                },
            )
            return None


@dataclass
class RawToolDef:
    """Raw tool definition from MCP tools/list."""

    name: str
    description: str
    inputSchema: dict  # noqa: N815 - MCP protocol field name.


class MCPClient:
    """Manages one MCP server via pluggable transport.

    Usage:
        transport = StdioTransport(command="npx", args=["-y", "@anthropic/mcp-filesystem", "."])
        client = MCPClient(transport=transport)
        await client.start()
        tools = await client.list_tools()
        result = await client.call_tool("read_file", {"path": "/tmp/test"})
        await client.shutdown()
    """

    def __init__(
        self,
        command: str | None = None,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        transport: MCPTransport | None = None,
    ):
        if transport is not None:
            self._transport: MCPTransport = transport
        elif command is not None:
            self._transport = StdioTransport(command=command, args=args, env=env)
        else:
            raise ValueError("Either 'command' or 'transport' must be provided")

        self.command = command or getattr(transport, "command", "unknown")
        self.args = args or []
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._started = False

    # ── public API ─────────────────────────────────────────────

    async def start(self) -> None:
        """Connect transport and complete MCP initialize handshake."""
        if self._started:
            return

        await self._transport.connect()

        # Start reader loop
        self._reader_task = asyncio.create_task(self._reader_loop())

        # Initialize handshake
        init_result = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "myagent", "version": "0.1.0"},
        })
        logger.debug("MCP initialized: %s", init_result,
                     extra={"category": "system"})

        # Send initialized notification
        await self._send_notification("notifications/initialized", {})

        self._started = True
        logger.info("MCP server started: %s", self.command,
                    extra={"category": "system"})

    async def list_tools(self) -> list[RawToolDef]:
        """Call tools/list and return tool definitions."""
        result = await self._send_request("tools/list", {})
        raw_tools = result.get("tools", [])
        return [
            RawToolDef(
                name=t["name"],
                description=t.get("description", ""),
                inputSchema=t.get("inputSchema", {}),
            )
            for t in raw_tools
        ]

    async def call_tool(self, name: str, params: dict) -> dict:
        """Call tools/call and return raw result."""
        result = await self._send_request("tools/call", {
            "name": name,
            "arguments": params,
        })
        return result

    async def list_resources(self) -> list[dict]:
        """Call resources/list.

        Returns an empty list if the server does not support the resources
        capability (JSON-RPC method-not-found error). Other errors are logged
        and also result in an empty list — the server is still functional
        for tools even if resource listing fails.
        """
        try:
            result = await self._send_request("resources/list", {})
            return result.get("resources", [])
        except RuntimeError as e:
            err_msg = str(e)
            if "-32601" in err_msg or "Method not found" in err_msg:
                # Expected capability absence: resources are optional in MCP.
                logger.debug(
                    "MCP server does not support resources/list: %s", self.command,
                    extra={"category": "system"},
                )
            else:
                logger.error(
                    "MCP server resources/list failed: %s", err_msg[:200],
                    exc_info=True,
                    extra={
                        "category": "error",
                        "component": "mcp",
                        "context": "mcp.resources_list",
                    },
                )
            return []
        except Exception as e:
            logger.exception(
                "MCP server resources/list failed: %s", str(e)[:200],
                extra={
                    "category": "error",
                    "component": "mcp",
                    "context": "mcp.resources_list",
                },
            )
            return []

    async def list_prompts(self) -> list[dict]:
        """Call prompts/list and return prompt definitions (gap-2-03).

        Returns an empty list if the server does not support the prompts
        capability (JSON-RPC method-not-found error). Other errors are logged
        and also result in an empty list for graceful degradation.
        """
        try:
            result = await self._send_request("prompts/list", {})
            return result.get("prompts", [])
        except RuntimeError as e:
            err_msg = str(e)
            if "-32601" in err_msg or "Method not found" in err_msg:
                # Expected capability absence: prompts are optional in MCP.
                logger.debug(
                    "MCP server does not support prompts/list: %s", self.command,
                    extra={"category": "system"},
                )
            else:
                logger.error(
                    "MCP server prompts/list failed: %s", err_msg[:200],
                    exc_info=True,
                    extra={
                        "category": "error",
                        "component": "mcp",
                        "context": "mcp.prompts_list",
                    },
                )
            return []
        except Exception as e:
            logger.exception(
                "MCP server prompts/list failed: %s", str(e)[:200],
                extra={
                    "category": "error",
                    "component": "mcp",
                    "context": "mcp.prompts_list",
                },
            )
            return []

    async def read_resource(self, uri: str) -> dict:
        """Call resources/read and return the resource content (G6).

        Args:
            uri: The resource URI to read.

        Returns:
            Dict with 'contents' list containing resource content items.
        """
        result = await self._send_request("resources/read", {"uri": uri})
        return result

    async def get_prompt(self, name: str, arguments: dict | None = None) -> dict:
        """Call prompts/get and return the rendered prompt (G6).

        Args:
            name: The prompt name to invoke.
            arguments: Optional arguments to pass to the prompt template.

        Returns:
            Dict with 'messages' list containing the rendered prompt messages.
        """
        params: dict = {"name": name}
        if arguments:
            params["arguments"] = arguments
        result = await self._send_request("prompts/get", params)
        return result

    async def shutdown(self) -> None:
        """Close transport and release resources."""
        self._started = False

        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task

        await self._transport.close()

        logger.info("MCP server shut down", extra={"category": "system"})

    # ── internal ────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and wait for response."""
        req_id = self._next_id()
        request = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        }

        # Create future for this request
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        # Send via transport
        body = json.dumps(request, ensure_ascii=False)
        message = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"
        await self._transport.send(message.encode("utf-8"))

        # Wait for response
        try:
            result = await asyncio.wait_for(future, timeout=30.0)
            if "error" in result:
                raise RuntimeError(
                    f"MCP error: {result['error'].get('message', 'Unknown error')}"
                )
            return result.get("result", {})
        finally:
            self._pending.pop(req_id, None)

    async def _send_notification(self, method: str, params: dict) -> None:
        """Send a JSON-RPC notification (no response expected)."""
        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        body = json.dumps(notification, ensure_ascii=False)
        message = (
            f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"
        )
        await self._transport.send(message.encode("utf-8"))

    async def _reader_loop(self) -> None:
        """Read JSON-RPC messages from transport."""
        try:
            buffer = b""
            while True:
                chunk = await self._transport.receive()
                if not chunk:
                    break
                buffer += chunk

                # Parse messages using Content-Length framing
                while b"\r\n\r\n" in buffer:
                    header_end = buffer.find(b"\r\n\r\n")
                    header = buffer[:header_end].decode("utf-8")
                    buffer = buffer[header_end + 4:]

                    content_length = 0
                    for line in header.split("\r\n"):
                        if line.lower().startswith("content-length:"):
                            content_length = int(line.split(":", 1)[1].strip())

                    if content_length == 0:
                        continue

                    if len(buffer) < content_length:
                        # Incomplete message, put header back and wait
                        buffer = (
                            header.encode("utf-8") + b"\r\n\r\n" + buffer
                        )
                        break

                    body = buffer[:content_length].decode("utf-8")
                    buffer = buffer[content_length:]

                    try:
                        message = json.loads(body)
                        await self._handle_message(message)
                    except json.JSONDecodeError as e:
                        logger.error(
                            "Invalid JSON from MCP server: %s",
                            e,
                            exc_info=True,
                            extra={
                                "category": "error",
                                "component": "mcp",
                                "context": "mcp.reader_invalid_json",
                            },
                        )

        except asyncio.CancelledError:
            logger.exception(
                "MCP reader loop cancelled",
                extra={
                    "category": "error",
                    "component": "mcp",
                    "context": "mcp reader loop cancellation",
                },
            )
            pass
        except Exception as e:
            logger.error(
                "MCP reader loop error: %s",
                e,
                exc_info=True,
                extra={"category": "error", "component": "mcp", "context": "mcp.reader_loop"},
            )

    async def _handle_message(self, message: dict) -> None:
        """Route incoming JSON-RPC message to pending request or ignore."""
        msg_id = message.get("id")
        if msg_id is not None and msg_id in self._pending:
            self._pending[msg_id].set_result(message)
        # Notifications and unknown IDs are ignored
