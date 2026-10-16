import httpx
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
    assert "super-secret" not in result.output


def test_status_reports_bad_configuration(tmp_path):
    result = runner.invoke(app, ["status"], env={"KDVOPS_WORKSPACE": str(tmp_path)})
    assert result.exit_code == 2
    assert "Configuration error" in result.output


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
