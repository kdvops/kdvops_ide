import httpx

from kdvops.rule_client import RuleAgentClient


def test_rule_client_translates_structured_rule_response():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "session_id": "rule-session",
                "reply": "Encontré 2 resultados en namespace demo.",
                "items": [{"name": "api-1"}],
            },
        )

    settings = type("Settings", (), {"rule_endpoint": "http://rules", "timeout": 5})()
    client = RuleAgentClient(settings, transport=httpx.MockTransport(handler))

    reply = client.complete([{"role": "user", "content": "muestra los pods en demo"}])

    assert reply["role"] == "assistant"
    assert "Encontré 2 resultados" in reply["content"]
    assert '"name": "api-1"' in reply["content"]
    assert client.session_id == "rule-session"
    assert requests[0].url.path == "/api/chat"


def test_rule_client_redacts_secrets_before_http_request():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"reply": "Solicitud recibida"})

    settings = type("Settings", (), {"rule_endpoint": "http://rules", "timeout": 5})()
    client = RuleAgentClient(settings, transport=httpx.MockTransport(handler))

    client.complete([{"role": "user", "content": "token=secret-value-123456789"}])

    sent = requests[0].content.decode()
    assert "secret-value-123456789" not in sent
    assert "__KDVOPS_MASK_" in sent
