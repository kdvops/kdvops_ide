import httpx
import pytest
import typer
from typer.testing import CliRunner

from kdvops.cli import app, chat_loop
from kdvops.config import Settings

runner = CliRunner()


def test_status_hides_token(tmp_path):
    result = runner.invoke(
        app,
        ["status"],
        env={
            "OPENCLAW_API_URL": "https://example.test/v1",
            "OPENCLAW_TOKEN": "super-secret",
            "KDVOPS_WORKSPACE": str(tmp_path),
        },
    )
    assert result.exit_code == 0
    assert "configured" in result.output
    assert "Tool iterations: unlimited" in result.output
    assert "super-secret" not in result.output


def test_status_reports_bad_configuration(tmp_path):
    result = runner.invoke(app, ["status"], env={"KDVOPS_WORKSPACE": str(tmp_path)})
    assert result.exit_code == 2
    assert "Configuration error" in result.output


def test_doctor_reports_compatible_gateway(tmp_path, monkeypatch):
    monkeypatch.setattr("kdvops.cli.AgentClient.probe", lambda self: ["openclaw/default"])
    result = runner.invoke(
        app,
        ["doctor"],
        env={
            "OPENCLAW_API_URL": "https://example.test/v1",
            "OPENCLAW_TOKEN": "token",
            "KDVOPS_WORKSPACE": str(tmp_path),
        },
    )
    assert result.exit_code == 0
    assert "compatible" in result.output
    assert "openclaw/default" in result.output


def test_doctor_reports_disabled_gateway_endpoint(tmp_path, monkeypatch):
    def fail(self):
        raise ValueError("endpoint appears disabled")

    monkeypatch.setattr("kdvops.cli.AgentClient.probe", fail)
    result = runner.invoke(
        app,
        ["doctor"],
        env={
            "OPENCLAW_API_URL": "https://example.test/v1",
            "OPENCLAW_TOKEN": "token",
            "KDVOPS_WORKSPACE": str(tmp_path),
        },
    )
    assert result.exit_code == 1
    assert "disabled" in result.output


def test_once_mode_executes_tool_cycle(tmp_path, monkeypatch):
    replies = iter(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "function": {"name": "list_files", "arguments": "{}"},
                    }
                ],
            },
            {"role": "assistant", "content": "done"},
        ]
    )
    monkeypatch.setattr("kdvops.cli.AgentClient.complete", lambda self, messages: next(replies))
    settings = Settings("https://example.test/v1", "token", "model", tmp_path)
    chat_loop(settings, once="inspect")


def test_once_mode_does_not_cap_tool_cycles(tmp_path, monkeypatch):
    calls = 0

    def complete(self, messages):
        nonlocal calls
        calls += 1
        if calls > 101:
            return {"role": "assistant", "content": "done"}
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call-{calls}",
                    "function": {
                        "name": "list_files",
                        "arguments": f'{{"directory":"round-{calls}"}}',
                    },
                }
            ],
        }

    monkeypatch.setattr("kdvops.cli.AgentClient.complete", complete)
    settings = Settings("https://example.test/v1", "token", "model", tmp_path)

    chat_loop(settings, once="inspect")

    assert calls == 102


def test_once_mode_stops_repeated_tool_cycle(tmp_path, monkeypatch):
    calls = 0

    def complete(self, messages):
        nonlocal calls
        calls += 1
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": f"call-{calls}",
                    "function": {"name": "list_files", "arguments": "{}"},
                }
            ],
        }

    monkeypatch.setattr("kdvops.cli.AgentClient.complete", complete)
    settings = Settings("https://example.test/v1", "token", "model", tmp_path)

    with pytest.raises(typer.Exit):
        chat_loop(settings, once="inspect")

    assert calls == 5


def test_run_returns_one_on_connection_error(tmp_path, monkeypatch):
    def fail(self, messages):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("kdvops.cli.AgentClient.complete", fail)
    result = runner.invoke(
        app,
        ["run", "inspect"],
        env={
            "OPENCLAW_API_URL": "https://example.test/v1",
            "OPENCLAW_TOKEN": "token",
            "KDVOPS_WORKSPACE": str(tmp_path),
        },
    )
    assert result.exit_code == 1
    assert "Connection or protocol error" in result.output
