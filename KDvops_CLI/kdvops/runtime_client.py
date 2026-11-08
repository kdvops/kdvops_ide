"""Lightweight terminal client for the local persistent KDvops runtime."""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from urllib.parse import quote, urlsplit

import httpx

DEFAULT_RUNTIME_URL = "http://127.0.0.1:8765"
TERMINAL_STATES = {"idle", "cancelled", "failed"}


def sanitize_terminal(value: object) -> str:
    """Remove terminal control bytes from untrusted runtime and model text."""
    text = str(value)
    return "".join(
        character
        for character in text
        if character in {"\n", "\t"} or (ord(character) >= 32 and not 0x7F <= ord(character) <= 0x9F)
    )


class RuntimeClientError(RuntimeError):
    """A safe, user-facing runtime API error."""


class RuntimeApiClient:
    """Small authenticated client for the loopback runtime API."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base_url = base_url.strip().rstrip("/")
        token = token.strip()
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("KDVOPS_RUNTIME_URL must be an HTTP(S) URL")
        if parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Cleartext KDVOPS_RUNTIME_URL is allowed only on loopback")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("KDVOPS_RUNTIME_URL must not contain credentials, query, or fragment")
        if not token:
            raise ValueError("KDVOPS_RUNTIME_TOKEN is required")
        if not math.isfinite(timeout) or not 0 < timeout <= 3600:
            raise ValueError("Runtime client timeout must be finite and between 0 and 3600 seconds")
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
            trust_env=False,
        )

    @classmethod
    def from_env(cls, *, base_url: str | None = None) -> RuntimeApiClient:
        timeout = float(os.getenv("KDVOPS_RUNTIME_CLIENT_TIMEOUT", "30"))
        return cls(
            base_url or os.getenv("KDVOPS_RUNTIME_URL", DEFAULT_RUNTIME_URL),
            os.getenv("KDVOPS_RUNTIME_TOKEN", ""),
            timeout=timeout,
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, *, payload: dict | None = None):
        try:
            response = self._client.request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise RuntimeClientError(f"Runtime connection failed: {type(exc).__name__}") from exc
        if response.is_error:
            try:
                detail = response.json().get("error", response.reason_phrase)
            except (ValueError, AttributeError):
                detail = response.reason_phrase
            safe_detail = sanitize_terminal(detail).replace("\n", " ")[:300]
            raise RuntimeClientError(f"Runtime returned HTTP {response.status_code}: {safe_detail}")
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeClientError("Runtime returned invalid JSON") from exc

    def health(self) -> dict:
        return self._request("GET", "/health")

    def list_sessions(self) -> list[dict]:
        return self._request("GET", "/v1/sessions")

    def create_session(self, title: str = "Terminal") -> dict:
        return self._request("POST", "/v1/sessions", payload={"title": title})

    @staticmethod
    def _session_path(session_id: str) -> str:
        if not session_id or len(session_id) > 128:
            raise ValueError("Session ID must be non-empty bounded text")
        return f"/v1/sessions/{quote(session_id, safe='')}"

    def get_session(self, session_id: str) -> dict:
        return self._request("GET", self._session_path(session_id))

    def send_message(self, session_id: str, content: str) -> dict:
        return self._request("POST", self._session_path(session_id) + "/messages", payload={"content": content})

    def stop(self, session_id: str) -> dict:
        return self._request("POST", self._session_path(session_id) + "/stop", payload={})

    def events(self, session_id: str, after: int = 0) -> list[dict]:
        after = max(0, int(after))
        return self._request("GET", self._session_path(session_id) + f"/events?after={after}")


HELP = """Commands:
  /new [title]     create and select a conversation
  /sessions        list conversations
  /use <id>        select an existing conversation
  /status          show the selected conversation
  /events          show its event journal
  /stop            cancel queued or running work
  /help            show this help
  /exit             close the terminal
Any other text is sent to the selected conversation."""


def _session_line(session: dict) -> str:
    queued = session.get("queued_messages", 0)
    return f"{session.get('id', '?')}  [{session.get('status', '?')}]  queued={queued}  {session.get('title', '')}"


def _follow_run(
    api: RuntimeApiClient,
    session_id: str,
    queue_id: int,
    *,
    output_fn: Callable[[str], None],
    sleep_fn: Callable[[float], None],
    poll_interval: float,
) -> None:
    """Poll incremental events until the current run reaches a terminal state."""
    after = 0
    while True:
        events = api.events(session_id, after)
        for event in events:
            after = max(after, int(event.get("id", 0)))
            kind = event.get("kind", "event")
            payload = event.get("payload") or {}
            if payload.get("queue_id") != queue_id:
                continue
            if kind == "tool.started":
                output_fn(sanitize_terminal(f"→ tool: {payload.get('name', '?')}"))
            elif kind == "tool.completed":
                output_fn(sanitize_terminal(f"✓ tool: {payload.get('name', '?')}"))
            elif kind == "message.completed":
                output_fn(sanitize_terminal(payload.get("content") or "(empty response)"))
            elif kind == "message.cancelled":
                output_fn("Run cancelled.")
            elif kind == "run.failed":
                output_fn(sanitize_terminal(f"Run failed: {payload.get('error', 'unknown error')}"))
            elif kind == "run.status" and payload.get("status") in TERMINAL_STATES:
                return
        if not events:
            sleep_fn(poll_interval)


def run_terminal(
    api: RuntimeApiClient,
    *,
    session_id: str | None = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    sleep_fn: Callable[[float], None] = time.sleep,
    poll_interval: float = 0.5,
    local_context=None,
) -> None:
    """Run the interactive terminal protocol using an injected API client."""
    emit = lambda value: output_fn(sanitize_terminal(value))
    api.health()
    emit("KDvops terminal connected. Type /help for commands.")
    selected = session_id
    while True:
        prompt = f"kdvops[{selected[:8] if selected else '-'}]> "
        try:
            line = input_fn(prompt).strip()
        except (EOFError, StopIteration):
            break
        except KeyboardInterrupt:
            emit("Use /stop to cancel work or /exit to quit.")
            continue
        if not line:
            continue
        command, _, argument = line.partition(" ")
        argument = argument.strip()
        if command in {"/exit", "/quit"}:
            break
        if command == "/help":
            emit(HELP)
            continue
        if command == "/new":
            created = api.create_session(argument or "Terminal")
            selected = created["id"]
            emit(f"Selected session {selected}: {created.get('title', argument or 'Terminal')}")
            continue
        if command == "/sessions":
            sessions = api.list_sessions()
            emit("\n".join(_session_line(item) for item in sessions) or "No sessions.")
            continue
        if command == "/use":
            if not argument:
                emit("Usage: /use <session-id>")
                continue
            session = api.get_session(argument)
            selected = session["id"]
            emit(f"Selected session {selected}: {session.get('title', '')}")
            continue
        if command in {"/status", "/events", "/stop"} and not selected:
            emit("No session selected. Use /new or /use first.")
            continue
        if command == "/status":
            emit(_session_line(api.get_session(selected)))
            continue
        if command == "/events":
            events = api.events(selected)
            emit(
                "\n".join(
                    f"{event.get('id', '?')}  {event.get('kind', '?')}  {event.get('payload', {})}" for event in events
                )
                or "No events."
            )
            continue
        if command == "/stop":
            result = api.stop(selected)
            emit(f"Stop: {result.get('status', '?')} (queue={result.get('queue_id')})")
            continue
        if command.startswith("/"):
            emit(f"Unknown command: {command}. Type /help.")
            continue
        if local_context is not None:
            from .local_rules import handle_local_request

            local_result = handle_local_request(line, *local_context)
            if local_result.handled:
                emit(local_result.message)
                continue
        if selected is None:
            created = api.create_session("Terminal")
            selected = created["id"]
            emit(f"Selected session {selected}: {created.get('title', 'Terminal')}")
        queued = api.send_message(selected, line)
        queue_id = int(queued["queue_id"])
        emit(f"Queued message {queue_id}. Waiting for OpenClaw…")
        try:
            _follow_run(
                api,
                selected,
                queue_id,
                output_fn=emit,
                sleep_fn=sleep_fn,
                poll_interval=poll_interval,
            )
        except KeyboardInterrupt:
            result = api.stop(selected)
            emit(f"Cancellation requested: {result.get('status', '?')}")
