import json

import httpx
import pytest

from kdvops.agent import AgentClient, decode_protocol_message


class Settings:
    endpoint = "https://openclaw.example/v1"
    token = "test-token"
    model = "test-model"
    timeout = 1.0
    cf_access_client_id = "cf-client-id"
    cf_access_client_secret = "cf-client-secret"


def client_returning(payload, status=200, content_type="application/json"):
    def handler(request):
        if content_type == "application/json":
            return httpx.Response(status, json=payload)
        return httpx.Response(status, text=payload, headers={"content-type": content_type})

    transport = httpx.MockTransport(handler)
    return AgentClient(Settings(), transport=transport)


def test_client_passes_tls_verification_setting(monkeypatch):
    captured = {}

    def fake_client(**kwargs):
        captured.update(kwargs)
        return object()

    settings = Settings()
    settings.tls_verify = False
    monkeypatch.setattr("kdvops.agent.httpx.Client", fake_client)

    AgentClient(settings)._client()

    assert captured["verify"] is False


def test_complete_returns_message():
    client = client_returning(
        {"choices": [{"message": {"role": "assistant", "content": '{"action":"final","content":"hello"}'}}]}
    )
    assert client.complete([])["content"] == "hello"


def test_complete_accepts_plain_text_as_final_response():
    client = client_returning(
        {"choices": [{"message": {"role": "assistant", "content": "plain final answer"}}]}
    )

    assert client.complete([]) == {"role": "assistant", "content": "plain final answer"}


def test_complete_sends_broker_and_cloudflare_access_headers():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"action":"final","content":"ok"}'}}]},
        )

    AgentClient(Settings(), transport=httpx.MockTransport(handler)).complete([])

    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert requests[0].headers["User-Agent"] == "kdvops-cli/0.5.0"
    assert requests[0].headers["CF-Access-Client-Id"] == "cf-client-id"
    assert requests[0].headers["CF-Access-Client-Secret"] == "cf-client-secret"
    payload = json.loads(requests[0].content)
    assert "response_format" not in payload
    assert "tools" not in payload


def test_complete_reuses_stable_conversation_id():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"action":"final","content":"ok"}'}}]},
        )

    client = AgentClient(Settings(), transport=httpx.MockTransport(handler))
    client.complete([])
    client.complete([])
    bodies = [request.read().decode() for request in requests]
    assert client.conversation_id in bodies[0]
    assert client.conversation_id in bodies[1]


def test_complete_masks_outbound_data_without_restoring_replayed_marker():
    requests = []

    def handler(request):
        requests.append(request)
        body = json.loads(request.content)
        masked_email = body["messages"][0]["content"].split()[-1]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": json.dumps({"action": "final", "content": masked_email})}}
                ]
            },
        )

    client = AgentClient(Settings(), transport=httpx.MockTransport(handler))
    result = client.complete([{"role": "user", "content": "contact owner@example.com"}])

    assert b"owner@example.com" not in requests[0].content
    assert result["content"].startswith("__KDVOPS_MASK_")
    assert result["content"] != "owner@example.com"


def test_complete_never_restores_replayed_marker_in_tool_arguments():
    def handler(request):
        body = json.loads(request.content)
        masked_email = body["messages"][0]["content"].split()[-1]
        action = {
            "action": "tool",
            "name": "write_file",
            "arguments": {"path": "owners.txt", "content": masked_email},
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(action)}}]})

    client = AgentClient(Settings(), transport=httpx.MockTransport(handler))
    result = client.complete([{"role": "user", "content": "store owner@example.com"}])
    arguments = json.loads(result["tool_calls"][0]["function"]["arguments"])

    assert arguments["content"].startswith("__KDVOPS_MASK_")
    assert arguments["content"] != "owner@example.com"


@pytest.mark.parametrize("payload", [{}, {"choices": []}, {"choices": [{}]}, {"choices": [{"message": "bad"}]}])
def test_complete_rejects_invalid_protocol(payload):
    with pytest.raises((ValueError, TypeError), match="Invalid response"):
        client_returning(payload).complete([])


def test_decode_protocol_tool_request():
    message = decode_protocol_message(
        {"content": '{"action":"tool","name":"read_file","arguments":{"path":"README.md"}}'}
    )
    assert message["tool_calls"][0]["function"]["name"] == "read_file"
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"path": "README.md"}


def test_decode_protocol_accepts_read_only_kubectl_request():
    message = decode_protocol_message(
        {
            "content": (
                '{"action":"tool","name":"kubectl_read",'
                '"arguments":{"args":["get","pods","--all-namespaces","-o","wide"]}}'
            )
        }
    )

    assert message["tool_calls"][0]["function"]["name"] == "kubectl_read"
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {
        "args": ["get", "pods", "--all-namespaces", "-o", "wide"]
    }


@pytest.mark.parametrize(
    "content",
    [
        '{"action":"tool","name":"exec","arguments":{}}',
        '{"action":"final","content":"ok","extra":true}',
        '{"action":"tool","name":"read_file","arguments":[],"extra":true}',
    ],
)
def test_decode_protocol_rejects_malformed_or_unauthorized_actions(content):
    with pytest.raises(ValueError, match="KDvops"):
        decode_protocol_message({"content": content})


def test_decode_protocol_treats_non_json_as_final_without_creating_tool_calls():
    assert decode_protocol_message({"content": "not json"}) == {
        "role": "assistant",
        "content": "not json",
    }


@pytest.mark.parametrize(
    "content",
    [
        '{"action":"tool","name":"write_file","arguments":{"path":"a.txt","content":42}}',
        '{"action":"tool","name":"read_file","arguments":{"path":false}}',
        '{"action":"tool","name":"list_files","arguments":{"recursive":"yes"}}',
        '{"action":"tool","name":"http_request","arguments":{"method":1,"url":"https://example.com"}}',
        '{"action":"tool","name":"kubectl_read","arguments":{"args":"get pods"}}',
        '{"action":"tool","name":"kubectl_read","arguments":{"args":["get",1]}}',
    ],
)
def test_decode_protocol_rejects_invalid_argument_types(content):
    with pytest.raises(ValueError, match="arguments"):
        decode_protocol_message({"content": content})


def test_probe_returns_openclaw_models():
    client = client_returning({"data": [{"id": "openclaw/default"}]})
    assert client.probe() == ["openclaw/default"]


def test_probe_sends_broker_and_cloudflare_access_headers():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": "openclaw/default"}]})

    AgentClient(Settings(), transport=httpx.MockTransport(handler)).probe()

    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert requests[0].headers["User-Agent"] == "kdvops-cli/0.5.0"
    assert requests[0].headers["CF-Access-Client-Id"] == "cf-client-id"
    assert requests[0].headers["CF-Access-Client-Secret"] == "cf-client-secret"


def test_probe_explains_disabled_endpoint_html_response():
    client = client_returning("<html>dashboard</html>", content_type="text/html")
    with pytest.raises(ValueError, match="disabled"):
        client.probe()


@pytest.mark.parametrize("payload", [{}, {"data": {}}, {"data": [{"name": "missing-id"}]}])
def test_probe_rejects_invalid_models_response(payload):
    with pytest.raises(ValueError, match="Invalid models response"):
        client_returning(payload).probe()
