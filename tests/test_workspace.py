from pathlib import Path
import pytest
from kdvops.workspace import Workspace


def test_read_write(tmp_path):
    ws = Workspace(tmp_path)
    ws.write_file("a.txt", "hello")
    assert ws.read_file("a.txt") == "hello"


def test_reject_escape(tmp_path):
    ws = Workspace(tmp_path)
    for path in ("../outside", "/etc/passwd", "a/../../outside"):
        with pytest.raises(ValueError):
            ws.path(path)


def test_reject_symlink(tmp_path):
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret")
    (tmp_path / "link").symlink_to(outside)
    ws = Workspace(tmp_path)
    with pytest.raises(ValueError):
        ws.read_file("link")
    outside.unlink()


def test_missing_parent(tmp_path):
    with pytest.raises(ValueError):
        Workspace(tmp_path).write_file("missing/a.txt", "hi")
