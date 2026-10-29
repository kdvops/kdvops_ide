"""Local browser chat UI with server-side conversation persistence."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
import uvicorn

from .agent import AgentClient
from .cli import SYSTEM, execute_tool
from .workspace import Workspace

MAX_REQUEST_BYTES = 32_768
MAX_PROMPT_CHARS = 16_000


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ConversationStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        self.path = path
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
                    position INTEGER NOT NULL, payload TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                    UNIQUE(conversation_id, position)
                );
            """)
        self.path.chmod(0o600)

    def _connect(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def create(self, title: str = "Nueva conversación") -> dict:
        conversation_id, now = str(uuid4()), _now()
        with self._connect() as db:
            db.execute(
                "INSERT INTO conversations VALUES (?, ?, ?, ?)",
                (conversation_id, title, now, now),
            )
            db.execute(
                "INSERT INTO messages(conversation_id, position, payload) VALUES (?, 0, ?)",
                (conversation_id, json.dumps({"role": "system", "content": SYSTEM})),
            )
        return {"id": conversation_id, "title": title, "created_at": now, "updated_at": now}

    def list(self) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, title, created_at, updated_at FROM conversations ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(zip(("id", "title", "created_at", "updated_at"), row)) for row in rows]

    def messages(self, conversation_id: str) -> list[dict]:
        UUID(conversation_id)
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload FROM messages WHERE conversation_id = ? ORDER BY position",
                (conversation_id,),
            ).fetchall()
        if not rows:
            raise KeyError("Conversation not found")
        return [json.loads(row[0]) for row in rows]

    def append(self, conversation_id: str, messages: list[dict]) -> None:
        with self._connect() as db:
            start = db.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM messages WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()[0]
            if not db.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone():
                raise KeyError("Conversation not found")
            db.executemany(
                "INSERT INTO messages(conversation_id, position, payload) VALUES (?, ?, ?)",
                [(conversation_id, start + index, json.dumps(message)) for index, message in enumerate(messages)],
            )
            db.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conversation_id)
            )

    def set_title_from_prompt(self, conversation_id: str, prompt: str) -> None:
        title = " ".join(prompt.split())[:60] or "Nueva conversación"
        with self._connect() as db:
            db.execute(
                "UPDATE conversations SET title = ? WHERE id = ? AND title = 'Nueva conversación'",
                (title, conversation_id),
            )

    def delete(self, conversation_id: str) -> None:
        UUID(conversation_id)
        with self._connect() as db:
            if not db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,)).rowcount:
                raise KeyError("Conversation not found")


class WebApp:
    def __init__(self, settings, store: ConversationStore, web_token: str, agent_factory=AgentClient):
        if len(web_token) < 32:
            raise ValueError("KDVOPS_WEB_TOKEN must contain at least 32 characters")
        self.settings, self.store, self.agent_factory = settings, store, agent_factory
        self.web_token = web_token
        self.workspace = Workspace(
            settings.workspace,
            allow_sensitive_reads=settings.allow_sensitive_reads,
            allow_sensitive_writes=settings.allow_sensitive_writes,
        )
        self._locks: dict[str, asyncio.Lock] = {}

    async def _json(self, send, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]})
        await send({"type": "http.response.body", "body": body})

    async def _unauthorized(self, send) -> None:
        body = b'{"error":"Unauthorized"}'
        await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store"), (b"www-authenticate", b'Basic realm="KDvops Chat", charset="UTF-8"')]})
        await send({"type": "http.response.body", "body": body})

    def _authorized(self, scope) -> bool:
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        authorization = headers.get(b"authorization", b"")
        if not authorization.startswith(b"Basic "):
            return False
        try:
            credentials = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
            username, password = credentials.split(":", 1)
        except (ValueError, UnicodeError):
            return False
        return username == "kdvops" and hmac.compare_digest(password, self.web_token)

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

    @staticmethod
    def _validate_mutation_request(scope) -> None:
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_type = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        if content_type != b"application/json":
            raise ValueError("Content-Type must be application/json")
        origin = headers.get(b"origin")
        host = headers.get(b"host", b"").decode("ascii", errors="strict")
        if origin:
            parsed = urlsplit(origin.decode("ascii", errors="strict"))
            if parsed.scheme not in {"http", "https"} or parsed.netloc != host:
                raise ValueError("Cross-origin requests are not allowed")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        if not self._authorized(scope):
            return await self._unauthorized(send)
        method, path = scope["method"], scope["path"]
        try:
            if method in {"POST", "DELETE"}:
                self._validate_mutation_request(scope)
            if method == "GET" and path == "/":
                body = HTML.encode()
                await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/html; charset=utf-8"), (b"cache-control", b"no-store"), (b"content-security-policy", b"default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")]})
                await send({"type": "http.response.body", "body": body})
                return
            if method == "GET" and path == "/api/conversations":
                return await self._json(send, 200, self.store.list())
            if method == "POST" and path == "/api/conversations":
                return await self._json(send, 201, self.store.create())
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["api", "conversations"]:
                conversation_id = parts[2]
                if method == "GET":
                    public = [m for m in self.store.messages(conversation_id) if m["role"] in {"user", "assistant"} and m.get("content")]
                    return await self._json(send, 200, public)
                if method == "DELETE":
                    self.store.delete(conversation_id)
                    return await self._json(send, 200, {"ok": True})
            if len(parts) == 4 and parts[:2] == ["api", "conversations"] and parts[3] == "messages" and method == "POST":
                return await self._chat(parts[2], await self._body(receive), send)
            return await self._json(send, 404, {"error": "Not found"})
        except (KeyError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return await self._json(send, 400, {"error": str(exc)})

    async def _chat(self, conversation_id: str, payload: dict, send):
        prompt = payload.get("content")
        if set(payload) != {"content"} or not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
            raise ValueError("content must be non-empty bounded text")
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        if lock.locked():
            return await self._json(send, 409, {"error": "Conversation already has a request in progress"})
        async with lock:
            messages = self.store.messages(conversation_id)
            user_message = {"role": "user", "content": prompt.strip()}
            self.store.append(conversation_id, [user_message])
            self.store.set_title_from_prompt(conversation_id, prompt)
            messages.append(user_message)
            agent = self.agent_factory(self.settings, conversation_id=f"kdvops-web:{conversation_id}")
            added = []
            try:
                while True:
                    reply = await asyncio.to_thread(agent.complete, messages)
                    messages.append(reply); added.append(reply)
                    calls = reply.get("tool_calls") or []
                    if not calls:
                        self.store.append(conversation_id, added)
                        return await self._json(send, 200, {"message": reply.get("content") or ""})
                    for call in calls:
                        name = call["function"]["name"]
                        args = json.loads(call["function"]["arguments"])
                        if name in {"write_file", "http_request"} and (name == "write_file" or str(args.get("method", "GET")).upper() != "GET"):
                            result = "DENIED: approval-required actions are unavailable in web mode"
                        else:
                            result = await asyncio.to_thread(execute_tool, self.workspace, name, args, self.settings)
                        tool_message = {"role": "tool", "tool_call_id": call["id"], "content": result}
                        messages.append(tool_message); added.append(tool_message)
            except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
                if added:
                    self.store.append(conversation_id, added)
                return await self._json(send, 502, {"error": f"Agent error: {type(exc).__name__}: {exc}"})


def serve_web(settings) -> None:
    db_path = Path(os.getenv("KDVOPS_HISTORY_DB", "/home/kdvops/.local/share/kdvops/history.db"))
    host = os.getenv("KDVOPS_WEB_HOST", "127.0.0.1")
    port = int(os.getenv("KDVOPS_WEB_PORT", "8080"))
    token = os.getenv("KDVOPS_WEB_TOKEN", "")
    uvicorn.run(WebApp(settings, ConversationStore(db_path), token), host=host, port=port)


HTML = r'''<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>KDvops Chat</title><style>
:root{color-scheme:dark;--bg:#08111f;--panel:#101c2d;--line:#22344c;--text:#e8f0fa;--muted:#8fa5bd;--blue:#38bdf8}*{box-sizing:border-box}body{margin:0;font:15px system-ui;background:var(--bg);color:var(--text);height:100vh;display:grid;grid-template-columns:280px 1fr}aside{background:#0b1626;border-right:1px solid var(--line);padding:16px;overflow:auto}button{border:1px solid var(--line);background:#14243a;color:var(--text);border-radius:10px;padding:10px 12px;cursor:pointer}#new{width:100%;border-color:#2878a4}.chat{display:flex;justify-content:space-between;gap:8px;margin-top:8px;width:100%;text-align:left}.chat span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.chat.active{border-color:var(--blue)}main{display:grid;grid-template-rows:auto 1fr auto;min-width:0}header{padding:18px 24px;border-bottom:1px solid var(--line);font-weight:700}#messages{overflow:auto;padding:28px max(20px,calc((100% - 850px)/2));display:flex;flex-direction:column;gap:18px}.msg{max-width:85%;padding:13px 16px;border-radius:14px;white-space:pre-wrap;line-height:1.5}.user{align-self:flex-end;background:#075985}.assistant{align-self:flex-start;background:var(--panel);border:1px solid var(--line)}form{padding:18px max(20px,calc((100% - 850px)/2));display:flex;gap:10px;border-top:1px solid var(--line)}textarea{flex:1;resize:none;min-height:48px;max-height:160px;background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:12px;padding:13px;font:inherit}#send{background:#0284c7;border:0;padding:0 20px}.empty{margin:auto;color:var(--muted);text-align:center}@media(max-width:700px){body{grid-template-columns:1fr}aside{display:none}.msg{max-width:95%}}
</style></head><body><aside><button id="new">＋ Nueva conversación</button><div id="chats"></div></aside><main><header>KDvops Chat · Kubernetes read-only</header><section id="messages"><div class="empty">Pregunta por pods, estados, logs o reportes del clúster.</div></section><form id="form"><textarea id="input" placeholder="Escribe un mensaje…" required></textarea><button id="send">Enviar</button></form></main><script>
let current=null,busy=false;const chats=document.querySelector('#chats'),messages=document.querySelector('#messages'),input=document.querySelector('#input');
async function api(path,options={}){const r=await fetch(path,{headers:{'Content-Type':'application/json'},...options});const data=await r.json();if(!r.ok)throw new Error(data.error||'Error');return data}
function bubble(role,text){const d=document.createElement('div');d.className='msg '+role;d.textContent=text;messages.append(d);messages.scrollTop=messages.scrollHeight}
async function refresh(){const list=await api('/api/conversations');chats.replaceChildren();for(const c of list){const b=document.createElement('button');b.className='chat'+(c.id===current?' active':'');const s=document.createElement('span');s.textContent=c.title;b.append(s);b.onclick=()=>openChat(c.id);chats.append(b)}}
async function create(){const c=await api('/api/conversations',{method:'POST',body:'{}'});await refresh();await openChat(c.id)}
async function openChat(id){current=id;messages.replaceChildren();for(const m of await api('/api/conversations/'+id))bubble(m.role,m.content);await refresh();input.focus()}
document.querySelector('#new').onclick=create;document.querySelector('#form').onsubmit=async e=>{e.preventDefault();if(busy)return;if(!current)await create();const text=input.value.trim();if(!text)return;input.value='';bubble('user',text);busy=true;document.querySelector('#send').disabled=true;try{const r=await api('/api/conversations/'+current+'/messages',{method:'POST',body:JSON.stringify({content:text})});bubble('assistant',r.message);await refresh()}catch(err){bubble('assistant','Error: '+err.message)}finally{busy=false;document.querySelector('#send').disabled=false;input.focus()}};
input.onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();document.querySelector('#form').requestSubmit()}};(async()=>{const list=await api('/api/conversations');if(list.length)await openChat(list[0].id);else await create()})();
</script></body></html>'''
