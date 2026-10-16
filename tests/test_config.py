import pytest

from kdvops.config import Settings


def test_settings_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_API_URL", "https://example.test/v1/")
    monkeypatch.setenv("OPENCLAW_TOKEN", "token")
    monkeypatch.setenv("OPENCLAW_MODEL", "model")
    monkeypatch.setenv("KDVOPS_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("KDVOPS_ALLOW_SENSITIVE_READS", "true")
    monkeypatch.setenv("KDVOPS_ALLOW_SENSITIVE_WRITES", "yes")

    settings = Settings.from_env()

    assert settings.endpoint == "https://example.test/v1"
    assert settings.workspace == tmp_path
    assert settings.allow_sensitive_reads is True
    assert settings.allow_sensitive_writes is True


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
