"""Restricted ASGI broker between KDvops clients and an isolated Gateway."""

import asyncio
import hmac
import json
import os
import threading
import time
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import uvicorn

from .agent import TOOLS

ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})
CANONICAL_TOOLS = {tool["function"]["name"]: tool for tool in TOOLS}
MAX_MESSAGES = 100
MAX_MESSAGE_CHARS = 250_000
MAX_TOTAL_MESSAGE_CHARS = 500_000
MAX_TOOL_ARGUMENT_CHARS = 250_000


@dataclass(frozen=True)
class BrokerConfig:
    client_token: str
    upstream_token: str
    upstream_url: str
    upstream_model: str = "openclaw/kdvops"
    bind_host: str = "0.0.0.0"
    port: int = 8080
    max_body_bytes: int = 1_000_000
    max_response_bytes: int = 2_000_000
    requests_per_minute: int = 30
    max_concurrency: int = 8
    upstream_timeout: float = 120.0
    body_timeout: float = 10.0

    def __post_init__(self) -> None:
        if min(len(self.client_token), len(self.upstream_token)) < 32:
            raise ValueError("Broker tokens must contain at least 32 characters")
        if hmac.compare_digest(self.client_token, self.upstream_token):
            raise ValueError("Client and upstream tokens must be different")
        parsed = urlsplit(self.upstream_url)
        internal_http = parsed.scheme == "http" and parsed.hostname and (
            parsed.hostname.endswith(".svc.cluster.local") or parsed.hostname.endswith(".svc")
        )
        if parsed.scheme != "https" and not internal_http:
            raise ValueError("Upstream must use HTTPS or internal Kubernetes service DNS")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Upstream URL must not contain embedded credentials")
        if parsed.query or parsed.fragment or parsed.path.rstrip("/") != "/v1":
            raise ValueError("Upstream URL must end exactly in /v1")
        positive = (
            self.port,
            self.max_body_bytes,
            self.max_response_bytes,
            self.requests_per_minute,
            self.max_concurrency,
            self.upstream_timeout,
            self.body_timeout,
        )
        if any(value <= 0 for value in positive) or self.port > 65535:
            raise ValueError("Broker numeric limits must be positive and valid")

    @classmethod
    def from_env(cls) -> "BrokerConfig":
        client_token = os.getenv("KDVOPS_BROKER_TOKEN", "").strip()
        upstream_token = os.getenv("OPENCLAW_UPSTREAM_TOKEN", "").strip()
        upstream_url = os.getenv("OPENCLAW_UPSTREAM_URL", "").strip().rstrip("/")
        if not client_token or not upstream_token:
            raise ValueError("KDVOPS_BROKER_TOKEN and OPENCLAW_UPSTREAM_TOKEN are required")
        return cls(
            client_token=client_token,
            upstream_token=upstream_token,
            upstream_url=upstream_url,
            upstream_model=os.getenv("OPENCLAW_UPSTREAM_MODEL", "openclaw/kdvops").strip(),
            bind_host=os.getenv("KDVOPS_BROKER_HOST", "0.0.0.0").strip(),
            port=int(os.getenv("KDVOPS_BROKER_PORT", "8080")),
            max_body_bytes=int(os.getenv("KDVOPS_BROKER_MAX_BODY_BYTES", "1000000")),
            max_response_bytes=int(os.getenv("KDVOPS_BROKER_MAX_RESPONSE_BYTES", "2000000")),
            requests_per_minute=int(os.getenv("KDVOPS_BROKER_RATE_LIMIT", "30")),
            max_concurrency=int(os.getenv("KDVOPS_BROKER_MAX_CONCURRENCY", "8")),
            upstream_timeout=float(os.getenv("KDVOPS_BROKER_UPSTREAM_TIMEOUT", "120")),
            body_timeout=float(os.getenv("KDVOPS_BROKER_BODY_TIMEOUT", "10")),
        )


class RateLimiter:
    """Global fixed-window limiter; the deployment uses one client credential."""

    def __init__(self, limit: int, window_seconds: int = 60):
        if limit < 1:
            raise ValueError("Rate limit must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._started = time.monotonic()
        self._count = 0
        self._lock = threading.Lock()

    def allow(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        with self._lock:
            if current - self._started >= self.window_seconds or current < self._started:
                self._started, self._count = current, 0
            if self._count >= self.limit:
                return False
            self._count += 1
            return True


def authorize(header: str | None, config: BrokerConfig) -> None:
    expected = f"Bearer {config.client_token}"
    if header is None or not hmac.compare_digest(header.encode(), expected.encode()):
        raise PermissionError("Unauthorized")


def _exact_keys(value: dict, allowed: set[str], label: str) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"Unknown {label} fields: {', '.join(sorted(unknown))}")


def _validate_messages(messages: Any) -> list[dict]:
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        raise ValueError("messages must be a non-empty bounded array")
    result: list[dict] = []
    known_tool_calls: set[str] = set()
    total_chars = 0
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ALLOWED_ROLES:
            raise ValueError("Invalid message role or shape")
        role = message["role"]
        if role in {"system", "user"}:
            _exact_keys(message, {"role", "content"}, "message")
            content = message.get("content")
            if not isinstance(content, str) or len(content) > MAX_MESSAGE_CHARS:
                raise ValueError("Message content must be bounded text")
            clean = {"role": role, "content": content}
            total_chars += len(content)
        elif role == "assistant":
            _exact_keys(message, {"role", "content", "tool_calls"}, "assistant message")
            content = message.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError("Assistant content must be text or null")
            if isinstance(content, str):
                if len(content) > MAX_MESSAGE_CHARS:
                    raise ValueError("Assistant content must be bounded text")
                total_chars += len(content)
            calls = message.get("tool_calls", [])
            if not isinstance(calls, list) or len(calls) > len(CANONICAL_TOOLS):
                raise ValueError("Invalid assistant tool calls")
            clean_calls = []
            for call in calls:
                if not isinstance(call, dict):
                    raise TypeError("Invalid tool call")
                _exact_keys(call, {"id", "type", "function"}, "tool call")
                function = call.get("function")
                if not isinstance(function, dict):
                    raise TypeError("Invalid tool call function")
                _exact_keys(function, {"name", "arguments"}, "tool function")
                call_id, name, arguments = call.get("id"), function.get("name"), function.get("arguments")
                if (
                    call.get("type") != "function"
                    or not isinstance(call_id, str)
                    or not call_id
                    or call_id in known_tool_calls
                    or name not in CANONICAL_TOOLS
                    or not isinstance(arguments, str)
                    or len(arguments) > MAX_TOOL_ARGUMENT_CHARS
                ):
                    raise ValueError("Invalid or unauthorized tool call")
                parsed_arguments = json.loads(arguments)
                if not isinstance(parsed_arguments, dict):
                    raise TypeError("Tool arguments must encode an object")
                known_tool_calls.add(call_id)
                clean_calls.append(
                    {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
                )
            if not content and not clean_calls:
                raise ValueError("Assistant message must contain text or tool calls")
            clean = {"role": role, "content": content, "tool_calls": clean_calls}
        else:
            _exact_keys(message, {"role", "tool_call_id", "content"}, "tool message")
            call_id, content = message.get("tool_call_id"), message.get("content")
            if call_id not in known_tool_calls or not isinstance(content, str) or len(content) > MAX_MESSAGE_CHARS:
                raise ValueError("Invalid tool result")
            total_chars += len(content)
            clean = {"role": role, "tool_call_id": call_id, "content": content}
        if total_chars > MAX_TOTAL_MESSAGE_CHARS:
            raise ValueError("Total message content exceeds limit")
        result.append(clean)
    return result


def _canonicalize_tools(tools: Any) -> list[dict]:
    if tools is None:
        return []
    if not isinstance(tools, list) or len(tools) > len(CANONICAL_TOOLS):
        raise ValueError("Invalid tools array")
    requested: list[str] = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        name = function.get("name") if isinstance(function, dict) else None
        if name not in CANONICAL_TOOLS or name in requested:
            raise ValueError(f"Tool {name!r} is not allowed or is duplicated")
        requested.append(name)
    return [deepcopy(CANONICAL_TOOLS[name]) for name in requested]


def sanitize_chat_request(payload: Any, config: BrokerConfig) -> dict:
    if not isinstance(payload, dict):
        raise TypeError("JSON body must be an object")
    _exact_keys(
        payload,
        {"model", "messages", "tools", "tool_choice", "response_format", "stream", "user"},
        "request",
    )
    if payload.get("stream", False) is not False:
        raise ValueError("Streaming is not enabled on the restricted broker")
    messages = _validate_messages(payload.get("messages"))
    tools = _canonicalize_tools(payload.get("tools"))
    tool_choice = payload.get("tool_choice", "auto")
    if isinstance(tool_choice, dict):
        _exact_keys(tool_choice, {"type", "function"}, "tool_choice")
        function = tool_choice.get("function")
        if not isinstance(function, dict):
            raise TypeError("Invalid tool_choice")
        _exact_keys(function, {"name"}, "tool_choice function")
        selected = function.get("name")
        if tool_choice.get("type") != "function" or selected not in {item["function"]["name"] for item in tools}:
            raise ValueError("Selected tool is not allowed")
        tool_choice = {"type": "function", "function": {"name": selected}}
    elif tool_choice not in {"auto", "none", "required"}:
        raise ValueError("Invalid tool_choice")
    result = {
        "model": config.upstream_model,
        "messages": messages,
        "tools": tools,
        "tool_choice": tool_choice,
        "stream": False,
    }
    response_format = payload.get("response_format")
    if response_format is not None and response_format != {"type": "json_object"}:
        raise ValueError("Invalid response format")
    user = payload.get("user")
    if user is not None:
        if not isinstance(user, str) or not (0 < len(user) <= 128):
            raise ValueError("Invalid user identifier")
        result["user"] = user
    return result


class BrokerApp:
    def __init__(self, config: BrokerConfig, transport=None):
        self.config = config
        self.limiter = RateLimiter(config.requests_per_minute)
        self.auth_limiter = RateLimiter(config.requests_per_minute)
        self.transport = transport

    @staticmethod
    async def _response(send, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers = [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def _read_json(self, scope, receive) -> Any:
        headers: dict[bytes, list[bytes]] = {}
        for key, value in scope.get("headers", []):
            headers.setdefault(key.lower(), []).append(value)
        if (
            b"transfer-encoding" in headers
            or len(headers.get(b"content-length", [])) != 1
            or len(headers.get(b"content-type", [])) != 1
            or len(headers.get(b"authorization", [])) != 1
        ):
            raise ValueError("Ambiguous request framing")
        if headers.get(b"content-type", [b""])[0].split(b";", 1)[0].strip().lower() != b"application/json":
            raise ValueError("Content-Type must be application/json")
        try:
            declared = int(headers[b"content-length"][0])
        except (KeyError, ValueError) as exc:
            raise ValueError("Invalid Content-Length") from exc
        if declared < 1 or declared > self.config.max_body_bytes:
            raise ValueError("Request body size is invalid")
        chunks = bytearray()
        more = True
        try:
            async with asyncio.timeout(self.config.body_timeout):
                while more:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        raise ValueError("Client disconnected")
                    chunk = event.get("body", b"")
                    chunks.extend(chunk)
                    if len(chunks) > self.config.max_body_bytes:
                        raise ValueError("Request body exceeds limit")
                    more = event.get("more_body", False)
        except TimeoutError as exc:
            raise ValueError("Request body read timed out") from exc
        if len(chunks) != declared:
            raise ValueError("Content-Length mismatch")
        return json.loads(chunks)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        path, method = scope.get("path", ""), scope.get("method", "")
        if path == "/health" and method == "GET":
            await self._response(send, 200, {"ok": True, "status": "live"})
            return
        if (path, method) not in {("/v1/models", "GET"), ("/v1/chat/completions", "POST")}:
            await self._response(send, 404, {"error": {"message": "Not found", "type": "invalid_request_error"}})
            return
        if not self.auth_limiter.allow():
            await self._response(send, 429, {"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}})
            return
        raw_headers: dict[bytes, list[bytes]] = {}
        for key, value in scope.get("headers", []):
            raw_headers.setdefault(key.lower(), []).append(value)
        if len(raw_headers.get(b"authorization", [])) > 1:
            await self._response(send, 400, {"error": {"message": "Invalid Authorization header", "type": "invalid_request_error"}})
            return
        try:
            authorization = raw_headers.get(b"authorization", [b""])[0]
            authorize(authorization.decode(), self.config)
        except (PermissionError, UnicodeError):
            await self._response(send, 401, {"error": {"message": "Unauthorized", "type": "authentication_error"}})
            return
        if not self.limiter.allow():
            await self._response(send, 429, {"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}})
            return
        if path == "/v1/models":
            await self._response(
                send,
                200,
                {"object": "list", "data": [{"id": self.config.upstream_model, "object": "model", "owned_by": "kdvops"}]},
            )
            return
        try:
            safe_payload = sanitize_chat_request(await self._read_json(scope, receive), self.config)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            await self._response(send, 400, {"error": {"message": str(exc), "type": "invalid_request_error"}})
            return
        try:
            async with httpx.AsyncClient(
                timeout=self.config.upstream_timeout,
                follow_redirects=False,
                transport=self.transport,
            ) as client, client.stream(
                "POST",
                self.config.upstream_url + "/chat/completions",
                headers={"Authorization": f"Bearer {self.config.upstream_token}"},
                json=safe_payload,
            ) as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > self.config.max_response_bytes:
                        raise ValueError("Upstream response exceeds limit")
            output = json.loads(body)
            if not isinstance(output, dict):
                raise TypeError("Invalid upstream response")
        except (httpx.HTTPError, TypeError, ValueError, json.JSONDecodeError):
            await self._response(send, 502, {"error": {"message": "Upstream Gateway error", "type": "api_error"}})
            return
        await self._response(send, 200, output)


def serve(config: BrokerConfig) -> None:
    uvicorn.run(
        BrokerApp(config),
        host=config.bind_host,
        port=config.port,
        access_log=False,
        limit_concurrency=config.max_concurrency,
        timeout_keep_alive=5,
        h11_max_incomplete_event_size=16_384,
        server_header=False,
    )
