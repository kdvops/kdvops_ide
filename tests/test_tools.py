from kdvops.cli import execute_tool
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
