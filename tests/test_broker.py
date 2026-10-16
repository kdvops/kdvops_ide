import asyncio
import json

import httpx
import pytest

from kdvops.broker import (
    BrokerApp,
    BrokerConfig,
    RateLimiter,
    authorize,
    sanitize_chat_request,
)

CLIENT_TOKEN = "c" * 48
UPSTREAM_TOKEN = "u" * 48


@pytest.fixture
def config():
    return BrokerConfig(
        client_token=CLIENT_TOKEN,
        upstream_token=UPSTREAM_TOKEN,
        upstream_url="http://openclaw-gateway.kdvops-system.svc.cluster.local:18789/v1",
        upstream_model="openclaw/kdvops",
        max_body_bytes=1024,
        max_response_bytes=2048,
        requests_per_minute=20,
    )


def valid_payload():
    return {
        "model": "openclaw/default",
        "messages": [{"role": "user", "content": "inspect"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "malicious replacement",
                    "parameters": {"type": "object"},
                },
            }
        ],
        "tool_choice": "auto",
        "stream": False,
        "user": "kdvops:test",
    }


def request(app, method, path, **kwargs):
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://broker"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def test_authorize_uses_constant_token_comparison(config):
    authorize(f"Bearer {CLIENT_TOKEN}", config)
    with pytest.raises(PermissionError):
        authorize("Bearer wrong", config)
    with pytest.raises(PermissionError):
        authorize(None, config)


def test_sanitize_forces_agent_and_reconstructs_canonical_tool(config):
    result = sanitize_chat_request(valid_payload(), config)
    assert result["model"] == "openclaw/kdvops"
    assert result["messages"][0]["content"] == "inspect"
    tool = result["tools"][0]["function"]
    assert tool["name"] == "read_file"
    assert tool["description"] != "malicious replacement"
    assert "path" in tool["parameters"]["properties"]
    assert result["stream"] is False


@pytest.mark.parametrize("name", ["exec", "gateway", "session_status", "message"])
def test_sanitize_rejects_non_client_tools(config, name):
    payload = valid_payload()
    payload["tools"][0]["function"]["name"] = name
    with pytest.raises(ValueError, match="not allowed"):
        sanitize_chat_request(payload, config)


def test_sanitize_rejects_duplicates_and_unknown_request_fields(config):
    payload = valid_payload()
    payload["tools"].append(payload["tools"][0])
    with pytest.raises(ValueError, match="duplicated"):
        sanitize_chat_request(payload, config)
    payload = valid_payload()
    payload["store"] = True
    with pytest.raises(ValueError, match="Unknown request"):
        sanitize_chat_request(payload, config)


def test_sanitize_rejects_streaming(config):
    payload = valid_payload()
    payload["stream"] = True
    with pytest.raises(ValueError, match="Streaming"):
        sanitize_chat_request(payload, config)


@pytest.mark.parametrize(
    "messages",
    [
        [],
        "bad",
        [{"role": "admin", "content": "bad"}],
        [{"role": "user", "content": [{"type": "text", "text": "bad"}]}],
        [{"role": "user", "content": "ok", "metadata": {"secret": "x"}}],
        [{"role": "user", "content": "x" * 250_001}],
        [{"role": "tool", "tool_call_id": "fabricated", "content": "result"}],
    ],
)
def test_sanitize_rejects_invalid_messages(config, messages):
    payload = valid_payload()
    payload["messages"] = messages
    with pytest.raises(ValueError):
        sanitize_chat_request(payload, config)


def test_sanitize_accepts_valid_tool_history(config):
    payload = valid_payload()
    payload["messages"] = [
        {"role": "user", "content": "read it"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"README.md"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": "contents"},
    ]
    assert sanitize_chat_request(payload, config)["messages"][-1]["content"] == "contents"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"client_token": "short"},
        {"upstream_token": CLIENT_TOKEN},
        {"upstream_url": "http://example.com/v1"},
        {"upstream_url": "https://example.com/admin"},
        {"upstream_url": "https://example.com/v1?x=1"},
        {"upstream_url": "https://user:password@example.com/v1"},
        {"max_concurrency": 0},
        {"body_timeout": 0},
    ],
)
def test_broker_config_rejects_unsafe_values(kwargs):
    values = {
        "client_token": CLIENT_TOKEN,
        "upstream_token": UPSTREAM_TOKEN,
        "upstream_url": "https://gateway.example.com/v1",
    }
    values.update(kwargs)
    with pytest.raises(ValueError):
        BrokerConfig(**values)


def test_broker_config_from_env(monkeypatch):
    monkeypatch.setenv("KDVOPS_BROKER_TOKEN", CLIENT_TOKEN)
    monkeypatch.setenv("OPENCLAW_UPSTREAM_TOKEN", UPSTREAM_TOKEN)
    monkeypatch.setenv("OPENCLAW_UPSTREAM_URL", "https://gateway.example.com/v1/")
    monkeypatch.setenv("KDVOPS_BROKER_PORT", "9090")
    config = BrokerConfig.from_env()
    assert config.upstream_url == "https://gateway.example.com/v1"
    assert config.port == 9090


def test_rate_limiter_is_global_and_resets():
    limiter = RateLimiter(limit=2, window_seconds=60)
    assert limiter.allow(now=100) is True
    assert limiter.allow(now=101) is True
    assert limiter.allow(now=102) is False
    assert limiter.allow(now=161) is True
    with pytest.raises(ValueError):
        RateLimiter(0)


def test_http_health_models_and_auth(config):
    app = BrokerApp(config)
    assert request(app, "GET", "/health").json()["status"] == "live"
    assert request(app, "GET", "/v1/models").status_code == 401
    response = request(
        app, "GET", "/v1/models", headers={"Authorization": f"Bearer {CLIENT_TOKEN}"}
    )
    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "openclaw/kdvops"
    assert request(app, "GET", "/admin").status_code == 404


def test_http_chat_forwards_sanitized_request(config):
    captured = {}

    def upstream(request_):
        captured["request"] = json.loads(request_.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    app = BrokerApp(config, transport=httpx.MockTransport(upstream))
    response = request(
        app,
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {CLIENT_TOKEN}"},
        json=valid_payload(),
    )
    assert response.status_code == 200
    assert captured["request"]["model"] == "openclaw/kdvops"
    assert captured["request"]["tools"][0]["function"]["description"] != "malicious replacement"


def test_http_rejects_bad_content_type_and_bad_json(config):
    app = BrokerApp(config)
    auth = {"Authorization": f"Bearer {CLIENT_TOKEN}"}
    assert request(app, "POST", "/v1/chat/completions", headers=auth, content=b"{}").status_code == 400
    response = request(
        app,
        "POST",
        "/v1/chat/completions",
        headers={**auth, "Content-Type": "application/json"},
        content=b"not-json",
    )
    assert response.status_code == 400


def test_http_rejects_upstream_failure_and_oversized_response(config):
    auth = {"Authorization": f"Bearer {CLIENT_TOKEN}"}

    def failure(_request):
        return httpx.Response(500, json={"error": "no"})

    app = BrokerApp(config, transport=httpx.MockTransport(failure))
    assert request(app, "POST", "/v1/chat/completions", headers=auth, json=valid_payload()).status_code == 502

    def oversized(_request):
        return httpx.Response(200, content=b'{' + b'"x":"' + b"a" * 3000 + b'"}')

    app = BrokerApp(config, transport=httpx.MockTransport(oversized))
    assert request(app, "POST", "/v1/chat/completions", headers=auth, json=valid_payload()).status_code == 502


def test_read_json_rejects_ambiguous_framing(config):
    app = BrokerApp(config)

    async def receive():
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def check(headers):
        with pytest.raises(ValueError, match="framing"):
            await app._read_json({"headers": headers}, receive)

    asyncio.run(check([(b"content-type", b"application/json"), (b"transfer-encoding", b"chunked")]))
    asyncio.run(
        check(
            [
                (b"content-type", b"application/json"),
                (b"content-length", b"2"),
                (b"content-length", b"2"),
            ]
        )
    )


def test_sanitize_rejects_oversized_assistant_content(config):
    payload = valid_payload()
    payload["messages"] = [{"role": "assistant", "content": "x" * 250_001, "tool_calls": []}]
    with pytest.raises(ValueError, match="bounded"):
        sanitize_chat_request(payload, config)


def test_http_rate_limits_failed_authentication(config):
    limited = BrokerConfig(
        client_token=config.client_token,
        upstream_token=config.upstream_token,
        upstream_url=config.upstream_url,
        requests_per_minute=2,
    )
    app = BrokerApp(limited)
    assert request(app, "GET", "/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert request(app, "GET", "/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert request(app, "GET", "/v1/models", headers={"Authorization": "Bearer wrong"}).status_code == 429


def test_http_rejects_duplicate_critical_headers(config):
    app = BrokerApp(config)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://broker"
        ) as client:
            return await client.post(
                "/v1/chat/completions",
                headers=[
                    ("Authorization", f"Bearer {CLIENT_TOKEN}"),
                    ("Authorization", f"Bearer {CLIENT_TOKEN}"),
                    ("Content-Type", "application/json"),
                ],
                content=json.dumps(valid_payload()),
            )

    assert asyncio.run(run()).status_code == 400


def test_read_json_times_out(config):
    timed = BrokerConfig(
        client_token=config.client_token,
        upstream_token=config.upstream_token,
        upstream_url=config.upstream_url,
        body_timeout=0.01,
    )
    app = BrokerApp(timed)

    async def receive():
        await asyncio.sleep(1)
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def check():
        with pytest.raises(ValueError, match="timed out"):
            await app._read_json(
                {
                    "headers": [
                        (b"authorization", f"Bearer {CLIENT_TOKEN}".encode()),
                        (b"content-type", b"application/json"),
                        (b"content-length", b"2"),
                    ]
                },
                receive,
            )

    asyncio.run(check())
