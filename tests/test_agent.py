import httpx
import pytest

from kdvops.agent import AgentClient


class Settings:
    endpoint = "https://openclaw.example/v1"
    token = "test-token"
    model = "test-model"
    timeout = 1.0


def client_returning(payload, status=200):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=payload))
    return AgentClient(Settings(), transport=transport)


def test_complete_returns_message():
    client = client_returning({"choices": [{"message": {"role": "assistant", "content": "hello"}}]})
    assert client.complete([])["content"] == "hello"


@pytest.mark.parametrize("payload", [{}, {"choices": []}, {"choices": [{}]}, {"choices": [{"message": "bad"}]}])
def test_complete_rejects_invalid_protocol(payload):
    with pytest.raises((ValueError, TypeError), match="Invalid response"):
        client_returning(payload).complete([])
