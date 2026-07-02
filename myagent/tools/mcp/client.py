"""MCP subprocess client — JSON-RPC over stdio.

Manages lifecycle of one MCP server subprocess:
initialize → tools/list → (runtime calls) → shutdown

Design doc reference: §四 工具系统 — MCP 集成
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("myagent.tools.mcp")


@dataclass
class RawToolDef:
    """Raw tool definition from MCP tools/list."""

    name: str
    description: str
    inputSchema: dict


class MCPClient:
    """Manages one MCP server subprocess via stdio JSON-RPC.

    Usage:
        client = MCPClient(command="npx", args=["-y", "@anthropic/mcp-filesystem", "."])
        await client.start()
        tools = await client.list_tools()
        result = await client.call_tool("read_file", {"path": "/tmp/test"})
        await client.shutdown()
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
        self._request_id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._started = False

    # ── public API ─────────────────────────────────────────────

    async def start(self) -> None:
        """Spawn subprocess and complete MCP initialize handshake."""
        if self._started:
            return

        env = {**__import__("os").environ, **self.env}

        self._process = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # Start reader loop
        self._reader_task = asyncio.create_task(self._reader_loop())

        # Initialize handshake
        init_result = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "myagent", "version": "0.1.0"},
        })
        logger.debug("MCP initialized: %s", init_result)

        # Send initialized notification
        await self._send_notification("notifications/initialized", {})

        self._started = True
        logger.info("MCP server started: %s %s", self.command, " ".join(self.args))

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
        """Call resources/list."""
        result = await self._send_request("resources/list", {})
        return result.get("resources", [])

    async def shutdown(self) -> None:
        """Terminate the MCP server subprocess."""
        self._started = False

        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass

        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()
            except ProcessLookupError:
                pass

        logger.info("MCP server shut down")

    # ── internal ────────────────────────────────────────────────

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    async def _send_request(self, method: str, params: dict) -> dict:
        """Send a JSON-RPC request and wait for response."""
        if not self._process or self._process.stdin is None:
            raise RuntimeError("MCP client not started")

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

        # Send
        body = json.dumps(request, ensure_ascii=False)
        message = f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"
        self._process.stdin.write(message.encode("utf-8"))
        await self._process.stdin.drain()

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
        if not self._process or self._process.stdin is None:
            return

        notification = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
        }
        body = json.dumps(notification, ensure_ascii=False)
        message = (
            f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}"
        )
        self._process.stdin.write(message.encode("utf-8"))
        await self._process.stdin.drain()

    async def _reader_loop(self) -> None:
        """Read JSON-RPC messages from subprocess stdout."""
        if not self._process or self._process.stdout is None:
            return

        try:
            buffer = b""
            while True:
                chunk = await self._process.stdout.read(4096)
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
                        logger.warning("Invalid JSON from MCP server: %s", e)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("MCP reader loop error: %s", e)

    async def _handle_message(self, message: dict) -> None:
        """Route incoming JSON-RPC message to pending request or ignore."""
        msg_id = message.get("id")
        if msg_id is not None and msg_id in self._pending:
            self._pending[msg_id].set_result(message)
        # Notifications and unknown IDs are ignored
