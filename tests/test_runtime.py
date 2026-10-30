import asyncio
import threading
import time

import httpx
import pytest

from kdvops.config import Settings
from kdvops.runtime import RuntimeApp, RuntimeManager, RuntimeStore

TEST_TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-ABCDE"


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
    return store, manager, RuntimeApp(manager, TEST_TOKEN)


def request(app, method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://runtime"
        ) as client:
            headers = kwargs.pop("headers", {})
            headers.setdefault("Authorization", "Bearer " + TEST_TOKEN)
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
    started = threading.Event()
    release = threading.Event()

    class BlockingAgent(FakeAgent):
        def complete(self, messages):
            started.set()
            assert release.wait(2)
            return super().complete(messages)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = Settings("https://example.test/v1", "token", "model", workspace)
    store = RuntimeStore(tmp_path / "state" / "runtime.db")
    manager = RuntimeManager(settings, store, BlockingAgent)
    app = RuntimeApp(manager, TEST_TOKEN)
    session_id = request(app, "POST", "/v1/sessions", json={}).json()["id"]
    queue_id = request(
        app, "POST", f"/v1/sessions/{session_id}/messages", json={"content": "hola"}
    ).json()["queue_id"]
    assert started.wait(2)

    response = request(app, "POST", f"/v1/sessions/{session_id}/stop", json={})
    release.set()

    assert response.status_code == 202
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and store.queue_state(queue_id) == "running":
        time.sleep(0.01)
    assert store.queue_state(queue_id) == "cancelled"
    assert store.get_session(session_id)["status"] == "cancelled"
    assert "message.completed" not in [event["kind"] for event in store.events(session_id)]


def test_cancel_queued_item_before_worker_does_not_call_agent(tmp_path):
    calls = []

    class CountingAgent(FakeAgent):
        def complete(self, messages):
            calls.append(messages)
            return super().complete(messages)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = Settings("https://example.test/v1", "token", "model", workspace)
    store = RuntimeStore(tmp_path / "state" / "runtime.db")
    session_id = store.create_session()["id"]
    queue_id = store.enqueue(session_id, "hola")
    assert store.request_cancel(session_id) == queue_id

    RuntimeManager(settings, store, CountingAgent)

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and store.queue_state(queue_id) != "cancelled":
        time.sleep(0.01)
    assert calls == []
    assert store.queue_state(queue_id) == "cancelled"


def test_cancel_accepted_immediately_before_completion_wins_atomically(tmp_path):
    store = RuntimeStore(tmp_path / "state" / "runtime.db")
    session_id = store.create_session()["id"]
    queue_id = store.enqueue(session_id, "hola")
    assert store.claim_next(session_id) == (queue_id, "hola")
    assert store.request_cancel(session_id) == queue_id

    store.finish_run(
        session_id,
        queue_id,
        "completed",
        messages=[
            {"role": "user", "content": "hola"},
            {"role": "assistant", "content": "respuesta tardía"},
        ],
        event_kind="message.completed",
        event_payload={"content": "respuesta tardía"},
    )

    assert store.queue_state(queue_id) == "cancelled"
    assert store.get_session(session_id)["status"] == "cancelled"
    assert store.messages(session_id)[-1] == {"role": "user", "content": "hola"}
    assert "message.completed" not in [event["kind"] for event in store.events(session_id)]


def test_cancel_accepted_before_operation_error_wins(tmp_path):
    store = RuntimeStore(tmp_path / "state" / "runtime.db")
    session_id = store.create_session()["id"]
    queue_id = store.enqueue(session_id, "hola")
    assert store.claim_next(session_id) == (queue_id, "hola")
    assert store.request_cancel(session_id) == queue_id

    store.finish_run(
        session_id,
        queue_id,
        "failed",
        messages=[{"role": "user", "content": "hola"}],
        event_kind="run.failed",
        event_payload={"error": "late error"},
    )

    assert store.queue_state(queue_id) == "cancelled"
    assert store.get_session(session_id)["status"] == "cancelled"
    kinds = [event["kind"] for event in store.events(session_id)]
    assert kinds[-3:] == ["run.cancel_requested", "message.cancelled", "run.status"]


def test_cancel_requested_event_is_committed_before_terminal_events(tmp_path):
    store = RuntimeStore(tmp_path / "state" / "runtime.db")
    session_id = store.create_session()["id"]
    queue_id = store.enqueue(session_id, "hola")
    assert store.claim_next(session_id) == (queue_id, "hola")

    assert store.request_cancel(session_id) == queue_id
    store.finish_run(
        session_id,
        queue_id,
        "cancelled",
        event_kind="message.cancelled",
        event_payload={"queue_id": queue_id},
    )

    kinds = [event["kind"] for event in store.events(session_id)]
    assert kinds.index("run.cancel_requested") < kinds.index("message.cancelled")
    assert kinds[-1] == "run.status"


def test_manager_recovers_queued_and_orphaned_running_items(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = Settings("https://example.test/v1", "token", "model", workspace)
    store = RuntimeStore(tmp_path / "state" / "runtime.db")
    first_session = store.create_session()["id"]
    second_session = store.create_session()["id"]
    first_id = store.enqueue(first_session, "queued")
    second_id = store.enqueue(second_session, "orphaned")
    assert store.claim_next(second_session) == (second_id, "orphaned")

    reopened = RuntimeStore(store.path)
    RuntimeManager(settings, reopened, FakeAgent)
    wait_for_idle(reopened, first_session)
    wait_for_idle(reopened, second_session)

    assert reopened.queue_state(first_id) == "completed"
    assert reopened.queue_state(second_id) == "completed"
    assert reopened.messages(second_session)[-1]["content"] == "context:orphaned"


def test_manager_fails_orphan_after_single_recovery_retry(tmp_path):
    store = RuntimeStore(tmp_path / "state" / "runtime.db")
    session_id = store.create_session()["id"]
    queue_id = store.enqueue(session_id, "orphaned")
    assert store.claim_next(session_id) == (queue_id, "orphaned")
    assert store.recover_pending() == [session_id]
    assert store.claim_next(session_id) == (queue_id, "orphaned")

    reopened = RuntimeStore(store.path)
    assert reopened.recover_pending() == []
    assert reopened.queue_state(queue_id) == "failed"
    assert reopened.get_session(session_id)["status"] == "failed"
    assert reopened.events(session_id)[-1]["payload"] == {"status": "failed"}


def test_recovery_preserves_cancel_accepted_before_retry_crash(tmp_path):
    store = RuntimeStore(tmp_path / "state" / "runtime.db")
    session_id = store.create_session()["id"]
    queue_id = store.enqueue(session_id, "orphaned")
    assert store.claim_next(session_id) == (queue_id, "orphaned")
    assert store.recover_pending() == [session_id]
    assert store.claim_next(session_id) == (queue_id, "orphaned")
    assert store.request_cancel(session_id) == queue_id

    reopened = RuntimeStore(store.path)
    assert reopened.recover_pending() == []
    assert reopened.queue_state(queue_id) == "cancelled"
    assert reopened.get_session(session_id)["status"] == "cancelled"
    kinds = [event["kind"] for event in reopened.events(session_id)]
    assert kinds[-3:] == ["run.cancel_requested", "message.cancelled", "run.status"]


@pytest.mark.parametrize(
    "token",
    [
        "REPLACE_WITH_A_RANDOM_32_CHARACTER_TOKEN",
        "replace-with-a-random-32-character-token",
        "a" * 32,
    ],
)
def test_runtime_rejects_placeholder_or_low_entropy_tokens(tmp_path, token):
    store, manager, _ = make_runtime(tmp_path)
    with pytest.raises(ValueError, match="random"):
        RuntimeApp(manager, token)
