import pytest

from kdvops.config import Settings


def test_settings_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_API_URL", "https://example.test/v1/")
    monkeypatch.setenv("OPENCLAW_TOKEN", "token")
    monkeypatch.setenv("OPENCLAW_MODEL", "model")
    monkeypatch.setenv("KDVOPS_TIMEOUT", "123")
    monkeypatch.setenv("KDVOPS_TLS_VERIFY", "false")
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", "client-id")
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("KDVOPS_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("KDVOPS_ALLOW_SENSITIVE_READS", "true")
    monkeypatch.setenv("KDVOPS_ALLOW_SENSITIVE_WRITES", "yes")
    monkeypatch.setenv("KDVOPS_HTTP_ENABLED", "true")
    monkeypatch.setenv("KDVOPS_HTTP_ALLOWED_HOSTS", "dev.azure.com,hooks.example.com")
    monkeypatch.setenv("AZURE_DEVOPS_PAT", "test-pat")

    settings = Settings.from_env()

    assert settings.endpoint == "https://example.test/v1"
    assert settings.workspace == tmp_path
    assert settings.timeout == 123
    assert settings.tls_verify is False
    assert settings.cf_access_client_id == "client-id"
    assert settings.cf_access_client_secret == "client-secret"
    assert settings.allow_sensitive_reads is True
    assert settings.allow_sensitive_writes is True
    assert settings.http_enabled is True
    assert settings.http_allowed_hosts == ("dev.azure.com", "hooks.example.com")
    assert settings.azure_devops_pat == "test-pat"


def test_settings_verifies_tls_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_API_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENCLAW_TOKEN", "token")
    monkeypatch.setenv("KDVOPS_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("KDVOPS_TLS_VERIFY", raising=False)

    assert Settings.from_env().tls_verify is True


def test_settings_allows_tls_bypass_without_additional_guards(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_API_URL", "https://gateway.example.test/v1")
    monkeypatch.setenv("OPENCLAW_TOKEN", "token")
    monkeypatch.setenv("KDVOPS_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("KDVOPS_TLS_VERIFY", "false")

    assert Settings.from_env().tls_verify is False


@pytest.mark.parametrize("url", ["http://example.test/v1", "ftp://example.test", ""])
def test_settings_rejects_insecure_endpoint(tmp_path, monkeypatch, url):
    monkeypatch.setenv("OPENCLAW_API_URL", url)
    monkeypatch.setenv("OPENCLAW_TOKEN", "token")
    monkeypatch.setenv("KDVOPS_WORKSPACE", str(tmp_path))
    with pytest.raises(ValueError, match="HTTPS"):
        Settings.from_env()


def test_settings_requires_token(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_API_URL", "https://example.test/v1")
    monkeypatch.delenv("OPENCLAW_TOKEN", raising=False)
    monkeypatch.setenv("KDVOPS_WORKSPACE", str(tmp_path))
    with pytest.raises(ValueError, match="TOKEN"):
        Settings.from_env()


def test_settings_requires_positive_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_API_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENCLAW_TOKEN", "token")
    monkeypatch.setenv("KDVOPS_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("KDVOPS_TIMEOUT", "0")

    with pytest.raises(ValueError, match="KDVOPS_TIMEOUT"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("client_id", "client_secret"),
    [("client-id", ""), ("", "client-secret")],
)
def test_settings_requires_complete_cloudflare_access_pair(
    tmp_path, monkeypatch, client_id, client_secret
):
    monkeypatch.setenv("OPENCLAW_API_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENCLAW_TOKEN", "token")
    monkeypatch.setenv("KDVOPS_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("CF_ACCESS_CLIENT_ID", client_id)
    monkeypatch.setenv("CF_ACCESS_CLIENT_SECRET", client_secret)

    with pytest.raises(ValueError, match="CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET"):
        Settings.from_env()
