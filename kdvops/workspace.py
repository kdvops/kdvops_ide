"""Filesystem sandbox enforced by canonical paths and symlink rejection."""
import os
from pathlib import Path

MAX_READ = 250_000
MAX_WRITE = 250_000


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
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
        if not path.is_file():
            raise ValueError("Not a file")
        if path.stat().st_size > MAX_READ:
            raise ValueError("File exceeds read limit")
        return path.read_text(encoding="utf-8")

    def write_file(self, name: str, content: str) -> None:
        if len(content.encode("utf-8")) > MAX_WRITE:
            raise ValueError("File exceeds write limit")
        target = self.path(name)
        if not target.parent.is_dir():
            raise ValueError("Parent directory does not exist")
        if target.exists() and not target.is_file():
            raise ValueError("Target is not a regular file")
        # O_NOFOLLOW prevents final-component symlink traversal; no privilege escalation.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(target, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
