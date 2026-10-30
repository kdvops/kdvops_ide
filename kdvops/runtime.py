"""Persistent local runtime for interactive KDvops clients.

The runtime owns conversation state and tool execution inside the container.  A
lightweight terminal client can enqueue input, inspect events, and request
cooperative cancellation without receiving broker credentials.
"""

from __future__ import annotations

import hmac
import json
import os
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import httpx
import uvicorn

from .agent import AgentClient
from .cli import SYSTEM, check_repeated_tool_round, execute_tool
from .workspace import Workspace

MAX_REQUEST_BYTES = 32_768
MAX_PROMPT_CHARS = 16_000


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RuntimeStore:
    """SQLite-backed sessions, message queue, history, and event journal."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        self.path = path
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS queued_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    content TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
            """)
        self.path.chmod(0o600)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA journal_mode = WAL")
        return db

    def create_session(self, title: str = "Nueva conversación") -> dict:
        session_id, now = str(uuid4()), _now()
        with self._connect() as db:
            db.execute(
                "INSERT INTO sessions VALUES (?, ?, 'idle', 0, ?, ?)",
                (session_id, title, now, now),
            )
            db.execute(
                "INSERT INTO messages(session_id, payload, created_at) VALUES (?, ?, ?)",
                (session_id, json.dumps({"role": "system", "content": SYSTEM}), now),
            )
        self.add_event(session_id, "session.created", {"status": "idle"})
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict:
        UUID(session_id)
        with self._connect() as db:
            row = db.execute(
                "SELECT id, title, status, cancel_requested, created_at, updated_at "
                "FROM sessions WHERE id = ?", (session_id,),
            ).fetchone()
            if not row:
                raise KeyError("Session not found")
            queued = db.execute(
                "SELECT COUNT(*) FROM queued_messages WHERE session_id = ? AND state = 'queued'",
                (session_id,),
            ).fetchone()[0]
        keys = ("id", "title", "status", "cancel_requested", "created_at", "updated_at")
        result = dict(zip(keys, row))
        result["cancel_requested"] = bool(result["cancel_requested"])
        result["queued_messages"] = queued
        return result

    def list_sessions(self) -> list[dict]:
        with self._connect() as db:
            ids = [row[0] for row in db.execute("SELECT id FROM sessions ORDER BY updated_at DESC")]
        return [self.get_session(session_id) for session_id in ids]

    def messages(self, session_id: str) -> list[dict]:
        self.get_session(session_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload FROM messages WHERE session_id = ? ORDER BY id", (session_id,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def append_messages(self, session_id: str, messages: list[dict]) -> None:
        now = _now()
        with self._connect() as db:
            db.executemany(
                "INSERT INTO messages(session_id, payload, created_at) VALUES (?, ?, ?)",
                [(session_id, json.dumps(message), now) for message in messages],
            )
            db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))

    def enqueue(self, session_id: str, content: str) -> int:
        self.get_session(session_id)
        now = _now()
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO queued_messages(session_id, content, created_at) VALUES (?, ?, ?)",
                (session_id, content, now),
            )
            db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            queue_id = cursor.lastrowid
        self.add_event(session_id, "message.queued", {"queue_id": queue_id, "content": content})
        return queue_id

    def claim_next(self, session_id: str) -> tuple[int, str] | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id, content FROM queued_messages "
                "WHERE session_id = ? AND state = 'queued' ORDER BY id LIMIT 1", (session_id,),
            ).fetchone()
            if row:
                db.execute("UPDATE queued_messages SET state = 'running' WHERE id = ?", (row[0],))
        return (row[0], row[1]) if row else None

    def finish_queue_item(self, queue_id: int, state: str) -> None:
        with self._connect() as db:
            db.execute("UPDATE queued_messages SET state = ? WHERE id = ?", (state, queue_id))

    def set_status(self, session_id: str, status: str, *, clear_cancel: bool = False) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE sessions SET status = ?, cancel_requested = CASE WHEN ? THEN 0 "
                "ELSE cancel_requested END, updated_at = ? WHERE id = ?",
                (status, clear_cancel, _now(), session_id),
            )
        self.add_event(session_id, "run.status", {"status": status})

    def request_cancel(self, session_id: str) -> None:
        self.get_session(session_id)
        with self._connect() as db:
            db.execute(
                "UPDATE sessions SET cancel_requested = 1, status = 'cancelling', updated_at = ? "
                "WHERE id = ?", (_now(), session_id),
            )
        self.add_event(session_id, "run.cancel_requested", {})

    def cancellation_requested(self, session_id: str) -> bool:
        return self.get_session(session_id)["cancel_requested"]

    def add_event(self, session_id: str, kind: str, payload: dict) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "INSERT INTO events(session_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                (session_id, kind, json.dumps(payload), _now()),
            )
        return cursor.lastrowid

    def events(self, session_id: str, after: int = 0) -> list[dict]:
        self.get_session(session_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, kind, payload, created_at FROM events "
                "WHERE session_id = ? AND id > ? ORDER BY id LIMIT 500", (session_id, after),
            ).fetchall()
        return [
            {"id": row[0], "kind": row[1], "payload": json.loads(row[2]), "created_at": row[3]}
            for row in rows
        ]


class RuntimeManager:
    def __init__(self, settings, store: RuntimeStore, agent_factory=AgentClient):
        self.settings, self.store, self.agent_factory = settings, store, agent_factory
        self.workspace = Workspace(
            settings.workspace,
            allow_sensitive_reads=settings.allow_sensitive_reads,
            allow_sensitive_writes=settings.allow_sensitive_writes,
        )
        self._workers: dict[str, threading.Thread] = {}
        self._guard = threading.Lock()

    def enqueue(self, session_id: str, content: str) -> int:
        queue_id = self.store.enqueue(session_id, content)
        self._ensure_worker(session_id)
        return queue_id

    def _ensure_worker(self, session_id: str) -> None:
        with self._guard:
            worker = self._workers.get(session_id)
            if worker and worker.is_alive():
                return
            worker = threading.Thread(target=self._worker, args=(session_id,), daemon=True)
            self._workers[session_id] = worker
            worker.start()

    def _worker(self, session_id: str) -> None:
        while True:
            item = self.store.claim_next(session_id)
            if item is None:
                # Close the enqueue/worker-exit race while holding the same guard
                # used by _ensure_worker.
                with self._guard:
                    item = self.store.claim_next(session_id)
                    if item is None:
                        self._workers.pop(session_id, None)
                        if self.store.get_session(session_id)["status"] not in {"cancelled", "failed"}:
                            self.store.set_status(session_id, "idle", clear_cancel=True)
                        return
            queue_id, prompt = item
            self._run_prompt(session_id, queue_id, prompt)

    def _run_prompt(self, session_id: str, queue_id: int, prompt: str) -> None:
        self.store.set_status(session_id, "running", clear_cancel=True)
        user_message = {"role": "user", "content": prompt}
        self.store.append_messages(session_id, [user_message])
        self.store.add_event(session_id, "message.accepted", {"queue_id": queue_id})
        messages = self.store.messages(session_id)
        agent = self.agent_factory(self.settings, conversation_id=f"kdvops-runtime:{session_id}")
        added: list[dict] = []
        previous_round = None
        repeated_rounds = 0
        deadline = time.monotonic() + self.settings.run_timeout
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Agent run exceeded the {self.settings.run_timeout:g}-second deadline"
                    )
                if self.store.cancellation_requested(session_id):
                    self.store.finish_queue_item(queue_id, "cancelled")
                    self.store.set_status(session_id, "cancelled", clear_cancel=True)
                    return
                reply = agent.complete(messages)
                messages.append(reply)
                added.append(reply)
                calls = reply.get("tool_calls") or []
                if not calls:
                    self.store.append_messages(session_id, added)
                    self.store.add_event(
                        session_id, "message.completed", {"content": reply.get("content") or ""},
                    )
                    self.store.finish_queue_item(queue_id, "completed")
                    self.store.set_status(session_id, "idle", clear_cancel=True)
                    return
                previous_round, repeated_rounds = check_repeated_tool_round(
                    calls, previous_round, repeated_rounds
                )
                for call in calls:
                    if self.store.cancellation_requested(session_id):
                        break
                    name = call["function"]["name"]
                    arguments = json.loads(call["function"]["arguments"])
                    self.store.add_event(session_id, "tool.started", {"name": name, "arguments": arguments})
                    # Approval-required actions stay denied until the terminal client's approval
                    # channel is implemented; the runtime never silently approves mutations.
                    if name == "write_file" or (
                        name == "http_request" and str(arguments.get("method", "GET")).upper() != "GET"
                    ):
                        result = "DENIED: interactive approval channel is not connected"
                    else:
                        result = execute_tool(self.workspace, name, arguments, self.settings)
                    tool_message = {"role": "tool", "tool_call_id": call["id"], "content": result}
                    messages.append(tool_message)
                    added.append(tool_message)
                    self.store.add_event(session_id, "tool.completed", {"name": name, "result": result})
        except (httpx.HTTPError, KeyError, ValueError, TypeError, RuntimeError, TimeoutError) as exc:
            if added:
                self.store.append_messages(session_id, added)
            self.store.finish_queue_item(queue_id, "failed")
            self.store.add_event(session_id, "run.failed", {"error": f"{type(exc).__name__}: {exc}"})
            self.store.set_status(session_id, "failed", clear_cancel=True)


class RuntimeApp:
    def __init__(self, manager: RuntimeManager, token: str):
        if len(token) < 32:
            raise ValueError("KDVOPS_RUNTIME_TOKEN must contain at least 32 characters")
        self.manager, self.store, self.token = manager, manager.store, token

    def _authorized(self, scope) -> bool:
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        supplied = headers.get(b"authorization", b"")
        return supplied.startswith(b"Bearer ") and hmac.compare_digest(
            supplied[7:].decode("ascii", errors="ignore"), self.token
        )

    async def _json(self, send, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})

    async def _body(self, receive) -> dict:
        data = bytearray()
        while True:
            event = await receive()
            data.extend(event.get("body", b""))
            if len(data) > MAX_REQUEST_BYTES:
                raise ValueError("Request too large")
            if not event.get("more_body", False):
                break
        value = json.loads(data or b"{}")
        if not isinstance(value, dict):
            raise TypeError("JSON body must be an object")
        return value

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        if not self._authorized(scope):
            return await self._json(send, 401, {"error": "Unauthorized"})
        method, path = scope["method"], scope["path"]
        try:
            if method == "GET" and path == "/health":
                return await self._json(send, 200, {"status": "ok"})
            if method == "GET" and path == "/v1/sessions":
                return await self._json(send, 200, self.store.list_sessions())
            if method == "POST" and path == "/v1/sessions":
                payload = await self._body(receive)
                title = payload.get("title", "Nueva conversación")
                if set(payload) - {"title"} or not isinstance(title, str) or len(title) > 100:
                    raise ValueError("title must be bounded text")
                return await self._json(send, 201, self.store.create_session(title or "Nueva conversación"))
            parts = path.strip("/").split("/")
            if len(parts) >= 3 and parts[:2] == ["v1", "sessions"]:
                session_id = parts[2]
                if len(parts) == 3 and method == "GET":
                    result = self.store.get_session(session_id)
                    result["messages"] = [
                        message for message in self.store.messages(session_id) if message["role"] != "system"
                    ]
                    return await self._json(send, 200, result)
                if len(parts) == 4 and parts[3] == "messages" and method == "POST":
                    payload = await self._body(receive)
                    content = payload.get("content")
                    if (
                        set(payload) != {"content"}
                        or not isinstance(content, str)
                        or not content.strip()
                        or len(content) > MAX_PROMPT_CHARS
                    ):
                        raise ValueError("content must be non-empty bounded text")
                    queue_id = self.manager.enqueue(session_id, content.strip())
                    return await self._json(send, 202, {"queue_id": queue_id, "status": "queued"})
                if len(parts) == 4 and parts[3] == "stop" and method == "POST":
                    self.store.request_cancel(session_id)
                    return await self._json(send, 202, {"status": "cancelling"})
                if len(parts) == 4 and parts[3] == "events" and method == "GET":
                    query = parse_qs(scope.get("query_string", b"").decode("ascii"))
                    after = int(query.get("after", ["0"])[0])
                    return await self._json(send, 200, self.store.events(session_id, after))
            return await self._json(send, 404, {"error": "Not found"})
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return await self._json(send, 400, {"error": str(exc)})


def serve_runtime(settings) -> None:
    db_path = Path(os.getenv("KDVOPS_RUNTIME_DB", "/home/kdvops/.local/share/kdvops/runtime.db"))
    host = os.getenv("KDVOPS_RUNTIME_HOST", "127.0.0.1")
    port = int(os.getenv("KDVOPS_RUNTIME_PORT", "8765"))
    token = os.getenv("KDVOPS_RUNTIME_TOKEN", "")
    store = RuntimeStore(db_path)
    uvicorn.run(RuntimeApp(RuntimeManager(settings, store), token), host=host, port=port)
