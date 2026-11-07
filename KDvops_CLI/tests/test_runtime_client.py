import json
import math

import httpx
import pytest
from typer.testing import CliRunner

from kdvops.cli import app
from kdvops.runtime_client import RuntimeApiClient, RuntimeClientError, run_terminal

runner = CliRunner()
TOKEN = "unit-test-token"


def test_runtime_api_client_sends_bearer_and_json():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/sessions" and request.method == "POST":
            return httpx.Response(201, json={"id": "session-1", "title": "Demo"})
        if request.url.path.endswith("/messages"):
            return httpx.Response(202, json={"queue_id": 7, "status": "queued"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    api = RuntimeApiClient(
        "http://127.0.0.1:8765",
        TOKEN,
        transport=httpx.MockTransport(handler),
    )
    assert api.health() == {"status": "ok"}
    assert api.create_session("Demo")["id"] == "session-1"
    assert api.send_message("session-1", "hola")["queue_id"] == 7
    api.close()

    assert all(request.headers["authorization"] == f"Bearer {TOKEN}" for request in requests)
    assert json.loads(requests[1].content) == {"title": "Demo"}
    assert json.loads(requests[2].content) == {"content": "hola"}


def test_runtime_api_client_rejects_cleartext_remote_url():
    with pytest.raises(ValueError, match="loopback"):
        RuntimeApiClient("http://example.test:8765", TOKEN)


@pytest.mark.parametrize("timeout", [math.nan, math.inf, -math.inf, 0, 3601])
def test_runtime_api_client_rejects_unsafe_timeout(timeout):
    with pytest.raises(ValueError, match="finite"):
        RuntimeApiClient("http://127.0.0.1:8765", TOKEN, timeout=timeout)


def test_runtime_api_client_escapes_session_id_as_one_path_segment():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path)
        return httpx.Response(404, json={"error": "Not found"})

    api = RuntimeApiClient(
        "http://127.0.0.1:8765",
        TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RuntimeClientError):
        api.get_session("../health")
    assert seen == [b"/v1/sessions/..%2Fhealth"]


def test_runtime_api_client_reports_server_error_without_leaking_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Unauthorized"})

    api = RuntimeApiClient(
        "http://127.0.0.1:8765",
        TOKEN,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RuntimeClientError) as caught:
        api.health()
    assert "401" in str(caught.value)
    assert TOKEN not in str(caught.value)


class FakeRuntimeApi:
    def __init__(self):
        self.sent = []
        self.stopped = []
        self.event_calls = 0

    def health(self):
        return {"status": "ok"}

    def create_session(self, title):
        return {"id": "session-1", "title": title, "status": "idle"}

    def list_sessions(self):
        return [{"id": "session-1", "title": "Terminal", "status": "idle", "queued_messages": 0}]

    def get_session(self, session_id):
        return {
            "id": session_id,
            "title": "Terminal",
            "status": "idle",
            "queued_messages": 0,
            "messages": [{"role": "assistant", "content": "respuesta"}],
        }

    def send_message(self, session_id, content):
        self.sent.append((session_id, content))
        return {"queue_id": 9, "status": "queued"}

    def events(self, session_id, after=0):
        self.event_calls += 1
        if self.event_calls == 1:
            return []
        return [
            {
                "id": 11,
                "kind": "message.completed",
                "payload": {"queue_id": 9, "content": "respuesta"},
            },
            {
                "id": 12,
                "kind": "run.status",
                "payload": {"queue_id": 9, "status": "idle"},
            },
        ]

    def stop(self, session_id):
        self.stopped.append(session_id)
        return {"status": "cancelling", "queue_id": 9}

    def close(self):
        pass


def test_terminal_creates_session_sends_prompt_and_prints_answer():
    api = FakeRuntimeApi()
    commands = iter(["hola", "/exit"])
    output = []

    run_terminal(
        api,
        input_fn=lambda _: next(commands),
        output_fn=output.append,
        sleep_fn=lambda _: None,
        poll_interval=0,
    )

    assert api.sent == [("session-1", "hola")]
    assert any("session-1" in line for line in output)
    assert any("respuesta" in line for line in output)


def test_terminal_commands_list_use_status_and_stop():
    api = FakeRuntimeApi()
    commands = iter(["/sessions", "/use session-1", "/status", "/stop", "/exit"])
    output = []

    run_terminal(
        api,
        input_fn=lambda _: next(commands),
        output_fn=output.append,
        sleep_fn=lambda _: None,
        poll_interval=0,
    )

    assert api.stopped == ["session-1"]
    assert any("Terminal" in line for line in output)
    assert any("cancelling" in line for line in output)


def test_terminal_paginates_and_prints_only_its_queue_response():
    class PaginatedApi(FakeRuntimeApi):
        def events(self, session_id, after=0):
            historical = [
                {
                    "id": number,
                    "kind": "message.completed",
                    "payload": {"queue_id": 8, "content": "other-answer"},
                }
                for number in range(1, 1201)
            ]
            current = [
                {
                    "id": 1201,
                    "kind": "message.completed",
                    "payload": {"queue_id": 9, "content": "current-answer"},
                },
                {
                    "id": 1202,
                    "kind": "run.status",
                    "payload": {"queue_id": 9, "status": "idle"},
                },
            ]
            return [event for event in historical + current if event["id"] > after][:500]

    api = PaginatedApi()
    commands = iter(["hola", "/exit"])
    output = []
    run_terminal(
        api,
        input_fn=lambda _: next(commands),
        output_fn=output.append,
        sleep_fn=lambda _: None,
        poll_interval=0,
    )
    assert "current-answer" in output
    assert "other-answer" not in output


def test_terminal_strips_control_sequences_from_remote_output():
    class ControlApi(FakeRuntimeApi):
        def events(self, session_id, after=0):
            return [
                {
                    "id": 1,
                    "kind": "message.completed",
                    "payload": {"queue_id": 9, "content": "safe\x1b]52;clipboard\x07text"},
                },
                {
                    "id": 2,
                    "kind": "run.status",
                    "payload": {"queue_id": 9, "status": "idle"},
                },
            ]

    output = []
    commands = iter(["hola", "/exit"])
    run_terminal(
        ControlApi(),
        input_fn=lambda _: next(commands),
        output_fn=output.append,
        sleep_fn=lambda _: None,
        poll_interval=0,
    )
    assert not any("\x1b" in line or "\x07" in line for line in output)


def test_client_command_requires_runtime_token(tmp_path):
    result = runner.invoke(
        app,
        ["client"],
        env={
            "KDVOPS_RUNTIME_TOKEN": "",
            "KDVOPS_WORKSPACE": str(tmp_path),
        },
    )
    assert result.exit_code == 2
    assert "KDVOPS_RUNTIME_TOKEN" in result.output
