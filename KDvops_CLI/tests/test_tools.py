from kdvops.cli import MAX_DIFF_CHARS, execute_tool
from kdvops.config import Settings
from kdvops.workspace import Workspace


def test_write_denied(tmp_path, monkeypatch):
    monkeypatch.setattr("kdvops.cli.Confirm.ask", lambda *a, **kw: False)
    ws = Workspace(tmp_path)
    result = execute_tool(ws, "write_file", {"path": "new.txt", "content": "hi"})
    assert "DENIED" in result
    assert not (tmp_path / "new.txt").exists()


def test_write_approved(tmp_path, monkeypatch):
    monkeypatch.setattr("kdvops.cli.Confirm.ask", lambda *a, **kw: True)
    ws = Workspace(tmp_path)
    result = execute_tool(ws, "write_file", {"path": "new.txt", "content": "hi"})
    assert "APPROVED" in result
    assert (tmp_path / "new.txt").read_text() == "hi"


def test_oversized_diff_is_not_approvable(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "kdvops.cli.Confirm.ask",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not prompt")),
    )
    ws = Workspace(tmp_path)
    content = "line changed\n" * (MAX_DIFF_CHARS // 5)

    result = execute_tool(ws, "write_file", {"path": "large.txt", "content": content})

    assert "Diff exceeds display limit" in result
    assert not (tmp_path / "large.txt").exists()


def test_list_read_and_unknown_tools(tmp_path):
    (tmp_path / "hello.txt").write_text("hello")
    ws = Workspace(tmp_path)
    assert "hello.txt" in execute_tool(ws, "list_files", {})
    assert execute_tool(ws, "read_file", {"path": "hello.txt"}) == "hello"
    assert execute_tool(ws, "missing", {}) == "Unknown tool"


def test_invalid_tool_arguments_return_error(tmp_path):
    result = execute_tool(Workspace(tmp_path), "read_file", {})
    assert result.startswith("ERROR: KeyError")


def test_non_get_http_requires_approval(tmp_path, monkeypatch):
    cfg = Settings(
        "https://gateway.example/v1",
        "token",
        "model",
        tmp_path,
        http_enabled=True,
        http_allowed_hosts=("hooks.example.com",),
    )
    monkeypatch.setattr("kdvops.cli.Confirm.ask", lambda *a, **kw: False)
    result = execute_tool(
        Workspace(tmp_path),
        "http_request",
        {"method": "POST", "url": "https://hooks.example.com/event", "json": {"ok": True}},
        cfg,
    )
    assert "DENIED" in result


def test_kubectl_read_dispatches_to_read_only_runner(tmp_path, monkeypatch):
    monkeypatch.setattr("kdvops.cli.kubectl_read", lambda args: "pod report")

    result = execute_tool(
        Workspace(tmp_path),
        "kubectl_read",
        {"args": ["get", "pods", "--all-namespaces"]},
    )

    assert result == "pod report"
