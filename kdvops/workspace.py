"""Filesystem sandbox enforced by canonical paths and symlink rejection."""
import os
import stat
import tempfile
from pathlib import Path

MAX_READ = 250_000
MAX_WRITE = 250_000
SENSITIVE_NAMES = {
    ".env",
    ".netrc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "kubeconfig",
}
SENSITIVE_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}


class Workspace:
    def __init__(
        self,
        root: Path,
        allow_sensitive_reads: bool = False,
        allow_sensitive_writes: bool = False,
    ):
        self.root = root.resolve(strict=True)
        self.allow_sensitive_reads = allow_sensitive_reads
        self.allow_sensitive_writes = allow_sensitive_writes
        if not self.root.is_dir():
            raise ValueError("Invalid workspace")

    def path(self, name: str) -> Path:
        if not name or '\x00' in name:
            raise ValueError("Invalid path")
        raw = Path(name)
        if raw.is_absolute():
            raise ValueError("Absolute paths forbidden")
        target = self.root / raw
        # Strict canonicalization also detects links in existing ancestors.
        canonical = target.resolve(strict=False)
        if not canonical.is_relative_to(self.root):
            raise ValueError("Path escapes workspace")
        if target.is_symlink():
            raise ValueError("Symlinks cannot be modified")
        return canonical

    def _guard_sensitive(self, path: Path, *, write: bool = False) -> None:
        if (write and self.allow_sensitive_writes) or (not write and self.allow_sensitive_reads):
            return
        name = path.name.lower()
        if (
            name in SENSITIVE_NAMES
            or name.startswith(".env.")
            or path.suffix.lower() in SENSITIVE_SUFFIXES
        ):
            raise ValueError(
                "Access to sensitive file is blocked; use the operation-specific "
                "KDVOPS_ALLOW_SENSITIVE_READS or KDVOPS_ALLOW_SENSITIVE_WRITES opt-in "
                "only for an intentional session"
            )

    def list_files(self, directory: str = ".") -> list[str]:
        folder = self.path(directory)
        if not folder.is_dir():
            raise ValueError("Not a directory")
        items = []
        for p in sorted(folder.iterdir()):
            if p.is_symlink():
                continue
            items.append(str(p.relative_to(self.root)) + ("/" if p.is_dir() else ""))
            if len(items) >= 200:
                break
        return items

    def read_file(self, name: str) -> str:
        path = self.path(name)
        self._guard_sensitive(path)
        if not path.is_file():
            raise ValueError("Not a file")
        if path.stat().st_size > MAX_READ:
            raise ValueError("File exceeds read limit")
        return path.read_text(encoding="utf-8")

    def write_file(self, name: str, content: str) -> None:
        if len(content.encode("utf-8")) > MAX_WRITE:
            raise ValueError("File exceeds write limit")
        target = self.path(name)
        self._guard_sensitive(target, write=True)
        if not target.parent.is_dir():
            raise ValueError("Parent directory does not exist")
        if target.exists() and not target.is_file():
            raise ValueError("Target is not a regular file")
        existing_mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
        fd, temporary_name = tempfile.mkstemp(prefix=".kdvops-", suffix=".tmp", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(fd, existing_mode)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, target)
            directory_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            temporary.unlink(missing_ok=True)
            raise
