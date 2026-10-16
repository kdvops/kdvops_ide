import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    endpoint: str
    token: str
    model: str
    workspace: Path
    timeout: float = 90.0
    allow_sensitive_reads: bool = False
    allow_sensitive_writes: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        endpoint = os.getenv("OPENCLAW_API_URL", "").strip().rstrip("/")
        token = os.getenv("OPENCLAW_TOKEN", "").strip()
        model = os.getenv("OPENCLAW_MODEL", "openclaw").strip()
        workspace = Path(os.getenv("KDVOPS_WORKSPACE", "/workspace")).resolve(strict=True)
        allow_sensitive_reads = os.getenv("KDVOPS_ALLOW_SENSITIVE_READS", "").strip().lower() in {
            "1", "true", "yes",
        }
        allow_sensitive_writes = os.getenv("KDVOPS_ALLOW_SENSITIVE_WRITES", "").strip().lower() in {
            "1", "true", "yes",
        }
        if not workspace.is_dir():
            raise ValueError("Workspace must be a directory")
        if not endpoint.startswith("https://") and not endpoint.startswith("http://127.0.0.1:"):
            raise ValueError("OPENCLAW_API_URL must be HTTPS (or localhost for development)")
        if not token:
            raise ValueError("OPENCLAW_TOKEN is required")
        return cls(
            endpoint,
            token,
            model,
            workspace,
            allow_sensitive_reads=allow_sensitive_reads,
            allow_sensitive_writes=allow_sensitive_writes,
        )
