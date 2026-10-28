import asyncio
import base64
import json

import httpx

from kdvops.config import Settings
from kdvops.web import ConversationStore, WebApp


class FakeAgent:
    def __init__(self, settings, conversation_id=None):
        self.conversation_id = conversation_id

    def complete(self, messages):
        users = [message["content"] for message in messages if message["role"] == "user"]
        return {"role": "assistant", "content": f"context:{'|'.join(users)}"}


def request(app, method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://web"
        ) as client:
            headers = kwargs.pop("headers", {})
            token = base64.b64encode(("kdvops:" + "t" * 32).encode()).decode()
            headers.setdefault("Authorization", f"Basic {token}")
            return await client.request(method, path, headers=headers, **kwargs)

    return asyncio.run(run())


def make_app(tmp_path):
    settings = Settings("https://example.test/v1", "token", "model", tmp_path)
    return WebApp(settings, ConversationStore(tmp_path / "history.db"), "t" * 32, FakeAgent)


def test_conversation_store_persists_history(tmp_path):
    path = tmp_path / "history.db"
    store = ConversationStore(path)
    conversation = store.create()
    store.append(conversation["id"], [{"role": "user", "content": "hola"}])

    reopened = ConversationStore(path)
    assert reopened.messages(conversation["id"])[-1]["content"] == "hola"
    assert (path.stat().st_mode & 0o777) == 0o600
    assert (tmp_path.stat().st_mode & 0o777) == 0o700


def test_web_chat_reuses_conversation_context(tmp_path):
    app = make_app(tmp_path)
    conversation = request(app, "POST", "/api/conversations", json={}).json()
    first = request(
        app,
        "POST",
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "uno"},
    )
    second = request(
        app,
        "POST",
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "dos"},
    )
    assert first.json()["message"] == "context:uno"
    assert second.json()["message"] == "context:uno|dos"

    history = request(app, "GET", f"/api/conversations/{conversation['id']}").json()
    assert [message["role"] for message in history] == ["user", "assistant", "user", "assistant"]


def test_web_rejects_invalid_payload_and_serves_csp(tmp_path):
    app = make_app(tmp_path)
    conversation = request(app, "POST", "/api/conversations", json={}).json()
    response = request(
        app,
        "POST",
        f"/api/conversations/{conversation['id']}/messages",
        content=json.dumps({"content": ""}),
    )
    assert response.status_code == 400
    home = request(app, "GET", "/")
    assert home.status_code == 200
    assert "content-security-policy" in home.headers


def test_web_rejects_cross_origin_mutation(tmp_path):
    app = make_app(tmp_path)
    response = request(
        app,
        "POST",
        "/api/conversations",
        headers={"Origin": "https://attacker.example"},
        json={},
    )
    assert response.status_code == 400
    assert "Cross-origin" in response.json()["error"]


def test_web_requires_authentication(tmp_path):
    app = make_app(tmp_path)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://web"
        ) as client:
            return await client.get("/")

    response = asyncio.run(run())
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")
