"""Filesystem sandbox enforced by canonical paths and symlink rejection."""
import os
import secrets
import stat
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
        self._root_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)

    def __del__(self):
        root_fd = getattr(self, "_root_fd", None)
        if root_fd is not None:
            try:
                os.close(root_fd)
            except OSError:
                pass
            self._root_fd = None

    def _open_parent(self, name: str) -> tuple[int, str]:
        if not name or '\x00' in name:
            raise ValueError("Invalid path")
        relative = Path(name)
        if relative.is_absolute():
            raise ValueError("Absolute paths forbidden")
        parts = relative.parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("Invalid path")

        parent_fd = os.dup(self._root_fd)
        try:
            for component in parts[:-1]:
                next_fd = os.open(
                    component,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
                os.close(parent_fd)
                parent_fd = next_fd
            return parent_fd, parts[-1]
        except OSError as exc:
            os.close(parent_fd)
            raise ValueError("Parent directory does not exist or is unsafe") from exc

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
        if self._is_sensitive(path):
            raise ValueError(
                "Access to sensitive file is blocked; use the operation-specific "
                "KDVOPS_ALLOW_SENSITIVE_READS or KDVOPS_ALLOW_SENSITIVE_WRITES opt-in "
                "only for an intentional session"
            )

    def _is_sensitive(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            relative = path
        for component in relative.parts:
            name = component.lower()
            if (
                name in SENSITIVE_NAMES
                or name.startswith(".env.")
                or Path(component).suffix.lower() in SENSITIVE_SUFFIXES
            ):
                return True
        return False

    def list_files(self, directory: str = ".", recursive: bool = False) -> list[str]:
        folder = self.path(directory)
        if not self.allow_sensitive_reads and self._is_sensitive(folder):
            raise ValueError("Access to sensitive directory is blocked")

        if directory == ".":
            folder_fd = os.dup(self._root_fd)
            prefix = Path()
        else:
            parent_fd, target_name = self._open_parent(directory)
            try:
                folder_fd = os.open(
                    target_name,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=parent_fd,
                )
            except OSError as exc:
                raise ValueError("Not a safe directory") from exc
            finally:
                os.close(parent_fd)
            prefix = Path(directory)

        items = []
        limit = 1000 if recursive else 200
        pending = [(folder_fd, prefix)]
        try:
            while pending and len(items) < limit:
                current_fd, current_prefix = pending.pop()
                try:
                    with os.scandir(current_fd) as entries:
                        for entry in entries:
                            relative = current_prefix / entry.name
                            display_path = self.root / relative
                            if entry.is_symlink() or (
                                not self.allow_sensitive_reads and self._is_sensitive(display_path)
                            ):
                                continue
                            is_directory = entry.is_dir(follow_symlinks=False)
                            items.append(str(relative) + ("/" if is_directory else ""))
                            if recursive and is_directory:
                                try:
                                    child_fd = os.open(
                                        entry.name,
                                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                        dir_fd=current_fd,
                                    )
                                except OSError:
                                    continue
                                pending.append((child_fd, relative))
                            if len(items) >= limit:
                                break
                finally:
                    os.close(current_fd)
            return items
        finally:
            for pending_fd, _ in pending:
                os.close(pending_fd)

    def read_file(self, name: str) -> str:
        path = self.path(name)
        self._guard_sensitive(path)
        parent_fd, target_name = self._open_parent(name)
        try:
            try:
                fd = os.open(target_name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            except OSError as exc:
                raise ValueError("Not a safe regular file") from exc
            try:
                file_stat = os.fstat(fd)
                if not stat.S_ISREG(file_stat.st_mode):
                    raise ValueError("Not a file")
                if file_stat.st_size > MAX_READ:
                    raise ValueError("File exceeds read limit")
                with os.fdopen(fd, "r", encoding="utf-8") as file:
                    fd = None
                    content = file.read(MAX_READ + 1)
                    if len(content.encode("utf-8")) > MAX_READ:
                        raise ValueError("File exceeds read limit")
                    return content
            finally:
                if fd is not None:
                    os.close(fd)
        finally:
            os.close(parent_fd)

    def file_exists(self, name: str) -> bool:
        path = self.path(name)
        self._guard_sensitive(path)
        parent_fd, target_name = self._open_parent(name)
        try:
            try:
                target_stat = os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            return stat.S_ISREG(target_stat.st_mode)
        finally:
            os.close(parent_fd)

    def write_file(self, name: str, content: str) -> None:
        if len(content.encode("utf-8")) > MAX_WRITE:
            raise ValueError("File exceeds write limit")
        target = self.path(name)
        self._guard_sensitive(target, write=True)

        parent_fd, target_name = self._open_parent(name)
        temporary_name = f".kdvops-{secrets.token_hex(16)}.tmp"
        fd = None
        try:
            try:
                target_stat = os.stat(target_name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                existing_mode = 0o600
            else:
                if not stat.S_ISREG(target_stat.st_mode):
                    raise ValueError("Target is not a regular file")
                existing_mode = stat.S_IMODE(target_stat.st_mode)

            fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                existing_mode,
                dir_fd=parent_fd,
            )
            os.fchmod(fd, existing_mode)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                fd = None
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(
                temporary_name,
                target_name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
        except BaseException:
            if fd is not None:
                os.close(fd)
            try:
                os.unlink(temporary_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            raise
        finally:
            os.close(parent_fd)
