import base64

import httpx
import pytest

from kdvops.config import Settings
from kdvops.http_tool import request_https


def public_resolver(host, port, type):
    return [(2, 1, 6, "", ("20.42.1.1", port))]


def settings(tmp_path, **overrides):
    values = {
        "endpoint": "https://gateway.example/v1",
        "token": "broker-token",
        "model": "openclaw/kdvops",
        "workspace": tmp_path,
        "http_enabled": True,
        "http_allowed_hosts": ("dev.azure.com", "hooks.example.com"),
        "azure_devops_pat": "test-pat",
    }
    values.update(overrides)
    return Settings(**values)


def test_azure_devops_get_injects_pat_only_locally(tmp_path):
    captured = {}

    def handler(request):
        captured.update(method=request.method, url=str(request.url), headers=request.headers)
        return httpx.Response(200, json={"count": 1})

    result = request_https(
        settings(tmp_path),
        "GET",
        "https://dev.azure.com/org/_apis/projects?api-version=7.1",
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
    )

    expected = base64.b64encode(b":test-pat").decode()
    assert captured["headers"]["Authorization"] == f"Basic {expected}"
    assert result == '{"count":1}'


def test_custom_allowlisted_host_never_receives_azure_pat(tmp_path):
    captured = {}

    def handler(request):
        captured.update(request.headers)
        return httpx.Response(200, text="ok")

    assert request_https(
        settings(tmp_path),
        "POST",
        "https://hooks.example.com/event",
        {"ok": True},
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
    ) == "ok"
    assert "Authorization" not in captured


@pytest.mark.parametrize(
    "url",
    [
        "http://dev.azure.com/org",
        "https://evil.example/org",
        "https://dev.azure.com:444/org",
        "https://user:password@dev.azure.com/org",
    ],
)
def test_http_rejects_insecure_or_unlisted_destinations(tmp_path, url):
    with pytest.raises(ValueError):
        request_https(settings(tmp_path), "GET", url, resolver=public_resolver)


def test_http_requires_explicit_opt_in(tmp_path):
    with pytest.raises(ValueError, match="disabled"):
        request_https(settings(tmp_path, http_enabled=False), "GET", "https://dev.azure.com/org", resolver=public_resolver)


def test_http_rejects_private_dns_resolution(tmp_path):
    def private_resolver(host, port, type):
        return [(2, 1, 6, "", ("127.0.0.1", port))]

    with pytest.raises(ValueError, match="non-public"):
        request_https(settings(tmp_path), "GET", "https://dev.azure.com/org", resolver=private_resolver)


def test_http_redacts_common_secrets(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"access_token": "top-secret", "count": 1})

    result = request_https(
        settings(tmp_path),
        "GET",
        "https://dev.azure.com/org",
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
    )
    assert "top-secret" not in result
    assert "[REDACTED]" in result


def test_http_redacts_configured_credentials_wherever_echoed(tmp_path):
    configured = settings(
        tmp_path,
        token="broker-secret",
        cf_access_client_id="cloudflare-client-id",
        cf_access_client_secret="cloudflare-secret",
        azure_devops_pat="azure-pat-secret",
    )
    encoded_pat = base64.b64encode(b":azure-pat-secret").decode()

    def handler(request):
        return httpx.Response(
            200,
            text=(
                "broker-secret cloudflare-client-id cloudflare-secret "
                f"azure-pat-secret {encoded_pat}"
            ),
        )

    result = request_https(
        configured,
        "GET",
        "https://dev.azure.com/org",
        transport=httpx.MockTransport(handler),
        resolver=public_resolver,
    )

    for secret in (
        "broker-secret",
        "cloudflare-client-id",
        "cloudflare-secret",
        "azure-pat-secret",
        encoded_pat,
    ):
        assert secret not in result
