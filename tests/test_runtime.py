import asyncio
import time

import httpx

from kdvops.config import Settings
from kdvops.runtime import RuntimeApp, RuntimeManager, RuntimeStore


class FakeAgent:
    def __init__(self, settings, conversation_id=None):
        self.conversation_id = conversation_id

    def complete(self, messages):
        prompts = [message["content"] for message in messages if message["role"] == "user"]
        return {"role": "assistant", "content": "context:" + "|".join(prompts)}


def make_runtime(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = Settings("https://example.test/v1", "token", "model", workspace)
    store = RuntimeStore(tmp_path / "state" / "runtime.db")
    manager = RuntimeManager(settings, store, FakeAgent)
    return store, manager, RuntimeApp(manager, "t" * 32)


def request(app, method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://runtime"
        ) as client:
            headers = kwargs.pop("headers", {})
            headers.setdefault("Authorization", "Bearer " + "t" * 32)
            return await client.request(method, path, headers=headers, **kwargs)

    return asyncio.run(run())


def wait_for_idle(store, session_id):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        session = store.get_session(session_id)
        if session["status"] == "idle" and session["queued_messages"] == 0:
            return
        time.sleep(0.01)
    raise AssertionError("runtime did not become idle")


def test_runtime_store_persists_sessions_queue_and_events(tmp_path):
    store = RuntimeStore(tmp_path / "state" / "runtime.db")
    session = store.create_session("Test")
    queue_id = store.enqueue(session["id"], "hola")

    reopened = RuntimeStore(store.path)

    assert reopened.get_session(session["id"])["queued_messages"] == 1
    assert reopened.claim_next(session["id"]) == (queue_id, "hola")
    assert [event["kind"] for event in reopened.events(session["id"])] == [
        "session.created",
        "message.queued",
    ]
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700


def test_runtime_processes_queued_messages_and_keeps_history(tmp_path):
    store, manager, _ = make_runtime(tmp_path)
    session = store.create_session()

    manager.enqueue(session["id"], "uno")
    wait_for_idle(store, session["id"])
    manager.enqueue(session["id"], "dos")
    wait_for_idle(store, session["id"])

    public = [message for message in store.messages(session["id"]) if message["role"] != "system"]
    assert [message["role"] for message in public] == ["user", "assistant", "user", "assistant"]
    assert public[-1]["content"] == "context:uno|dos"
    assert "message.completed" in [event["kind"] for event in store.events(session["id"])]


def test_runtime_http_api_creates_session_queues_and_reads_events(tmp_path):
    store, _, app = make_runtime(tmp_path)
    created = request(app, "POST", "/v1/sessions", json={})
    assert created.status_code == 201
    session_id = created.json()["id"]

    queued = request(
        app, "POST", f"/v1/sessions/{session_id}/messages", json={"content": "hola"}
    )
    assert queued.status_code == 202
    wait_for_idle(store, session_id)

    session = request(app, "GET", f"/v1/sessions/{session_id}").json()
    events = request(app, "GET", f"/v1/sessions/{session_id}/events?after=0").json()
    assert session["messages"][-1]["content"] == "context:hola"
    assert events[-1]["kind"] == "run.status"


def test_runtime_api_requires_local_bearer_token(tmp_path):
    _, _, app = make_runtime(tmp_path)
    response = request(app, "GET", "/health", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_runtime_stop_sets_cooperative_cancellation(tmp_path):
    store, _, app = make_runtime(tmp_path)
    session_id = request(app, "POST", "/v1/sessions", json={}).json()["id"]

    response = request(app, "POST", f"/v1/sessions/{session_id}/stop", json={})

    assert response.status_code == 202
    assert store.get_session(session_id)["status"] == "cancelling"
    assert store.get_session(session_id)["cancel_requested"] is True
