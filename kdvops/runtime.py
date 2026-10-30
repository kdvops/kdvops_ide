"""Persistent local runtime for interactive KDvops clients.

The runtime owns conversation state and tool execution inside the container.  A
lightweight terminal client can enqueue input, inspect events, and request
cooperative cancellation without receiving broker credentials.
"""

from __future__ import annotations

import hmac
import json
import os
import re
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
                    cancel_queue_id INTEGER,
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
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
            """)
            session_columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
            if "cancel_queue_id" not in session_columns:
                db.execute("ALTER TABLE sessions ADD COLUMN cancel_queue_id INTEGER")
            queue_columns = {row[1] for row in db.execute("PRAGMA table_info(queued_messages)")}
            if "attempts" not in queue_columns:
                db.execute(
                    "ALTER TABLE queued_messages ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
                )
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
                "INSERT INTO sessions(id, title, status, cancel_requested, cancel_queue_id, "
                "created_at, updated_at) VALUES (?, ?, 'idle', 0, NULL, ?, ?)",
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
                "SELECT id, title, status, cancel_queue_id, created_at, updated_at "
                "FROM sessions WHERE id = ?", (session_id,),
            ).fetchone()
            if not row:
                raise KeyError("Session not found")
            queued = db.execute(
                "SELECT COUNT(*) FROM queued_messages WHERE session_id = ? AND state = 'queued'",
                (session_id,),
            ).fetchone()[0]
        keys = ("id", "title", "status", "cancel_queue_id", "created_at", "updated_at")
        result = dict(zip(keys, row))
        result["cancel_requested"] = result["cancel_queue_id"] is not None
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
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "INSERT INTO queued_messages(session_id, content, created_at) VALUES (?, ?, ?)",
                (session_id, content, now),
            )
            db.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            queue_id = cursor.lastrowid
            db.execute(
                "INSERT INTO events(session_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                (
                    session_id,
                    "message.queued",
                    json.dumps({"queue_id": queue_id, "content": content}),
                    now,
                ),
            )
        return queue_id

    def claim_next(self, session_id: str) -> tuple[int, str] | None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id, content FROM queued_messages "
                "WHERE session_id = ? AND state = 'queued' ORDER BY id LIMIT 1", (session_id,),
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE queued_messages SET state = 'running', attempts = attempts + 1 "
                    "WHERE id = ?", (row[0],),
                )
                db.execute(
                    "UPDATE sessions SET status = 'running', updated_at = ? WHERE id = ?",
                    (_now(), session_id),
                )
        return (row[0], row[1]) if row else None

    def queue_state(self, queue_id: int) -> str:
        with self._connect() as db:
            row = db.execute("SELECT state FROM queued_messages WHERE id = ?", (queue_id,)).fetchone()
        if not row:
            raise KeyError("Queue item not found")
        return row[0]

    def recover_pending(self) -> list[str]:
        """Retry an orphaned read-only run once, then fail it on the next restart."""
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            exhausted = db.execute(
                "SELECT q.id, q.session_id, s.cancel_queue_id FROM queued_messages q "
                "JOIN sessions s ON s.id = q.session_id "
                "WHERE q.state = 'running' AND q.attempts >= 2"
            ).fetchall()
            for queue_id, session_id, cancel_queue_id in exhausted:
                state = "cancelled" if cancel_queue_id == queue_id else "failed"
                event_kind = "message.cancelled" if state == "cancelled" else "run.failed"
                payload = (
                    {"queue_id": queue_id}
                    if state == "cancelled"
                    else {"error": "Run recovery retry limit exceeded"}
                )
                db.execute(
                    "UPDATE queued_messages SET state = ? WHERE id = ?", (state, queue_id)
                )
                db.execute(
                    "INSERT INTO events(session_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                    (session_id, event_kind, json.dumps(payload), now),
                )
                db.execute(
                    "INSERT INTO events(session_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                    (session_id, "run.status", json.dumps({"status": state}), now),
                )
                db.execute(
                    "UPDATE sessions SET status = ?, cancel_requested = 0, "
                    "cancel_queue_id = NULL, updated_at = ? WHERE id = ?",
                    (state, now, session_id),
                )
            db.execute(
                "UPDATE queued_messages SET state = 'queued' "
                "WHERE state = 'running' AND attempts < 2"
            )
            rows = db.execute(
                "SELECT DISTINCT session_id FROM queued_messages WHERE state = 'queued'"
            ).fetchall()
        return [row[0] for row in rows]

    def finish_run(
        self,
        session_id: str,
        queue_id: int,
        state: str,
        *,
        messages: list[dict] | None = None,
        event_kind: str,
        event_payload: dict,
    ) -> None:
        """Atomically persist a run's messages, event, queue state, and session state."""
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cancel_queue_id = db.execute(
                "SELECT cancel_queue_id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()[0]
            if state in {"completed", "failed"} and cancel_queue_id == queue_id:
                state = "cancelled"
                event_kind = "message.cancelled"
                event_payload = {"queue_id": queue_id}
            if state == "cancelled" and messages:
                # Never retain a final answer or an unmatched assistant tool call
                # from a run whose cancellation was accepted.
                messages = [message for message in messages if message.get("role") == "user"]
            status = "idle" if state == "completed" else state
            if messages:
                db.executemany(
                    "INSERT INTO messages(session_id, payload, created_at) VALUES (?, ?, ?)",
                    [(session_id, json.dumps(message), now) for message in messages],
                )
            db.execute(
                "INSERT INTO events(session_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                (session_id, event_kind, json.dumps(event_payload), now),
            )
            db.execute(
                "INSERT INTO events(session_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                (session_id, "run.status", json.dumps({"status": status}), now),
            )
            db.execute("UPDATE queued_messages SET state = ? WHERE id = ?", (state, queue_id))
            db.execute(
                "UPDATE sessions SET status = ?, cancel_requested = 0, cancel_queue_id = NULL, "
                "updated_at = ? WHERE id = ?",
                (status, now, session_id),
            )

    def set_status(self, session_id: str, status: str, *, clear_cancel: bool = False) -> None:
        with self._connect() as db:
            db.execute(
                "UPDATE sessions SET status = ?, cancel_requested = CASE WHEN ? THEN 0 "
                "ELSE cancel_requested END, cancel_queue_id = CASE WHEN ? THEN NULL "
                "ELSE cancel_queue_id END, updated_at = ? WHERE id = ?",
                (status, clear_cancel, clear_cancel, _now(), session_id),
            )
        self.add_event(session_id, "run.status", {"status": status})

    def request_cancel(self, session_id: str) -> int | None:
        self.get_session(session_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT id FROM queued_messages WHERE session_id = ? "
                "AND state IN ('running', 'queued') ORDER BY CASE state WHEN 'running' THEN 0 ELSE 1 END, id LIMIT 1",
                (session_id,),
            ).fetchone()
            if not row:
                return None
            queue_id = row[0]
            db.execute(
                "UPDATE sessions SET cancel_requested = 1, cancel_queue_id = ?, "
                "status = 'cancelling', updated_at = ? WHERE id = ?",
                (queue_id, _now(), session_id),
            )
            db.execute(
                "INSERT INTO events(session_id, kind, payload, created_at) VALUES (?, ?, ?, ?)",
                (
                    session_id,
                    "run.cancel_requested",
                    json.dumps({"queue_id": queue_id}),
                    _now(),
                ),
            )
        return queue_id

    def cancellation_requested(self, session_id: str, queue_id: int) -> bool:
        return self.get_session(session_id)["cancel_queue_id"] == queue_id

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
        for session_id in self.store.recover_pending():
            self._ensure_worker(session_id)

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
                        if self.store.get_session(session_id)["status"] not in {
                            "idle",
                            "cancelled",
                            "failed",
                        }:
                            self.store.set_status(session_id, "idle", clear_cancel=True)
                        return
            queue_id, prompt = item
            self._run_prompt(session_id, queue_id, prompt)

    def _run_prompt(self, session_id: str, queue_id: int, prompt: str) -> None:
        self.store.set_status(session_id, "running")
        if self.store.cancellation_requested(session_id, queue_id):
            self.store.finish_run(
                session_id, queue_id, "cancelled",
                event_kind="message.cancelled", event_payload={"queue_id": queue_id},
            )
            return
        user_message = {"role": "user", "content": prompt}
        self.store.add_event(session_id, "message.accepted", {"queue_id": queue_id})
        messages = self.store.messages(session_id)
        messages.append(user_message)
        agent = self.agent_factory(self.settings, conversation_id=f"kdvops-runtime:{session_id}")
        added: list[dict] = [user_message]
        previous_round = None
        repeated_rounds = 0
        deadline = time.monotonic() + self.settings.run_timeout
        try:
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Agent run exceeded the {self.settings.run_timeout:g}-second deadline"
                    )
                if self.store.cancellation_requested(session_id, queue_id):
                    self.store.finish_run(
                        session_id, queue_id, "cancelled", messages=added,
                        event_kind="message.cancelled", event_payload={"queue_id": queue_id},
                    )
                    return
                reply = agent.complete(messages)
                messages.append(reply)
                added.append(reply)
                if self.store.cancellation_requested(session_id, queue_id):
                    self.store.finish_run(
                        session_id, queue_id, "cancelled", messages=added,
                        event_kind="message.cancelled", event_payload={"queue_id": queue_id},
                    )
                    return
                calls = reply.get("tool_calls") or []
                if not calls:
                    self.store.finish_run(
                        session_id, queue_id, "completed", messages=added,
                        event_kind="message.completed",
                        event_payload={"content": reply.get("content") or ""},
                    )
                    return
                previous_round, repeated_rounds = check_repeated_tool_round(
                    calls, previous_round, repeated_rounds
                )
                for call in calls:
                    if self.store.cancellation_requested(session_id, queue_id):
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
                    if self.store.cancellation_requested(session_id, queue_id):
                        self.store.finish_run(
                            session_id, queue_id, "cancelled", messages=added,
                            event_kind="message.cancelled", event_payload={"queue_id": queue_id},
                        )
                        return
                    tool_message = {"role": "tool", "tool_call_id": call["id"], "content": result}
                    messages.append(tool_message)
                    added.append(tool_message)
                    self.store.add_event(session_id, "tool.completed", {"name": name, "result": result})
        except (httpx.HTTPError, KeyError, ValueError, TypeError, RuntimeError, TimeoutError) as exc:
            self.store.finish_run(
                session_id, queue_id, "failed", messages=added,
                event_kind="run.failed", event_payload={"error": f"{type(exc).__name__}: {exc}"},
            )


class RuntimeApp:
    def __init__(self, manager: RuntimeManager, token: str):
        normalized = token.strip().lower()
        placeholder_markers = ("replace", "placeholder", "change-me", "changeme", "example")
        distinct = len(set(token))
        if (
            len(token) < 43
            or any(marker in normalized for marker in placeholder_markers)
            or distinct < 12
            or not re.fullmatch(r"[A-Za-z0-9_-]+", token)
        ):
            raise ValueError(
                "KDVOPS_RUNTIME_TOKEN must be a random URL-safe token of at least 32 bytes"
            )
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
                    queue_id = self.store.request_cancel(session_id)
                    status = "cancelling" if queue_id is not None else "idle"
                    return await self._json(send, 202, {"status": status, "queue_id": queue_id})
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
