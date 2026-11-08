from pathlib import Path

import httpx

from kdvops.local_rules import handle_local_request
from kdvops.workspace import Workspace


class Settings:
    rule_endpoint = "http://rules"
    timeout = 5


def test_analyze_directory_is_local(tmp_path):
    (tmp_path / "a.yaml").write_text("kind: Service\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("docs\n", encoding="utf-8")
    result = handle_local_request("analiza el directorio completo", Workspace(tmp_path), Settings(), None)
    assert result.handled is True
    assert "Archivos: 2" in result.message


def test_read_yaml_is_local(tmp_path):
    (tmp_path / "app.yaml").write_text("kind: Deployment\nspec:\n  replicas: 2\n", encoding="utf-8")
    result = handle_local_request("lee app.yaml", Workspace(tmp_path), Settings(), None)
    assert "YAML válido" in result.message
    assert "Deployment" in result.message


def test_modify_yaml_requires_confirmation_and_scans(monkeypatch, tmp_path):
    (tmp_path / "app.yaml").write_text("kind: Deployment\nspec:\n  replicas: 2\n", encoding="utf-8")
    monkeypatch.setattr("kdvops.local_rules.Confirm.ask", lambda *args, **kwargs: True)
    calls = []

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={"status": "clean", "scan": {"clean": True}})

    monkeypatch.setattr("kdvops.local_rules.httpx.post", post)
    result = handle_local_request("modifica app.yaml replicas a 3", Workspace(tmp_path), Settings(), None)
    assert "guardados" in result.message
    assert "replicas: 3" in (tmp_path / "app.yaml").read_text(encoding="utf-8")
    assert calls[0][0].endswith("/api/security/scan")


def test_create_manifest_asks_before_writing(monkeypatch, tmp_path):
    monkeypatch.setattr("kdvops.local_rules.Confirm.ask", lambda *args, **kwargs: False)

    def post(url, **kwargs):
        return httpx.Response(200, json={
            "status": "generated",
            "artifact": {"filename": "api.yaml", "content": "kind: Service\n"},
        })

    monkeypatch.setattr("kdvops.local_rules.httpx.post", post)
    result = handle_local_request("crea un manifiesto kubernetes llamado api", Workspace(tmp_path), Settings(), None)
    assert "rechazados" in result.message
    assert not (tmp_path / "api.yaml").exists()


def test_unknown_request_falls_back_to_rule_chatbot(tmp_path):
    result = handle_local_request("muestra los pods en ns demo", Workspace(tmp_path), Settings(), None)
    assert result.handled is False
