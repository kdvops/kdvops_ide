import httpx
import pytest

from kdvops.agent import AgentClient


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


def test_complete_returns_message():
    client = client_returning({"choices": [{"message": {"role": "assistant", "content": "hello"}}]})
    assert client.complete([])["content"] == "hello"


def test_complete_sends_broker_and_cloudflare_access_headers():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    AgentClient(Settings(), transport=httpx.MockTransport(handler)).complete([])

    assert requests[0].headers["Authorization"] == "Bearer test-token"
    assert requests[0].headers["CF-Access-Client-Id"] == "cf-client-id"
    assert requests[0].headers["CF-Access-Client-Secret"] == "cf-client-secret"


def test_complete_reuses_stable_conversation_id():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = AgentClient(Settings(), transport=httpx.MockTransport(handler))
    client.complete([])
    client.complete([])
    bodies = [request.read().decode() for request in requests]
    assert client.conversation_id in bodies[0]
    assert client.conversation_id in bodies[1]


@pytest.mark.parametrize("payload", [{}, {"choices": []}, {"choices": [{}]}, {"choices": [{"message": "bad"}]}])
def test_complete_rejects_invalid_protocol(payload):
    with pytest.raises((ValueError, TypeError), match="Invalid response"):
        client_returning(payload).complete([])


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
