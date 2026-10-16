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


@pytest.mark.parametrize(
    "name",
    [".env", ".env.production", "id_rsa", "deploy.key", "cert.pem", "kubeconfig", "credentials.json"],
)
def test_sensitive_files_are_blocked(tmp_path, name):
    (tmp_path / name).write_text("secret")
    with pytest.raises(ValueError, match="sensitive"):
        Workspace(tmp_path).read_file(name)


def test_sensitive_files_can_be_explicitly_enabled(tmp_path):
    (tmp_path / ".env").write_text("SAFE_FOR_TEST=yes")
    assert Workspace(tmp_path, allow_sensitive_reads=True).read_file(".env") == "SAFE_FOR_TEST=yes"


def test_sensitive_read_opt_in_does_not_allow_writes(tmp_path):
    (tmp_path / ".env").write_text("OLD=yes")
    with pytest.raises(ValueError, match="sensitive"):
        Workspace(tmp_path, allow_sensitive_reads=True).write_file(".env", "NEW=yes")


def test_sensitive_writes_require_separate_opt_in(tmp_path):
    Workspace(tmp_path, allow_sensitive_writes=True).write_file(".env", "NEW=yes")
    assert (tmp_path / ".env").read_text() == "NEW=yes"


def test_atomic_write_replaces_content_and_preserves_mode(tmp_path):
    target = tmp_path / "script.sh"
    target.write_text("old")
    target.chmod(0o750)

    Workspace(tmp_path).write_file("script.sh", "new")

    assert target.read_text() == "new"
    assert target.stat().st_mode & 0o777 == 0o750
    assert not list(tmp_path.glob(".kdvops-*.tmp"))
