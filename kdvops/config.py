import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    endpoint: str
    token: str
    model: str
    workspace: Path
    cf_access_client_id: str = ""
    cf_access_client_secret: str = ""
    timeout: float = 90.0
    allow_sensitive_reads: bool = False
    allow_sensitive_writes: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        endpoint = os.getenv("OPENCLAW_API_URL", "").strip().rstrip("/")
        token = os.getenv("OPENCLAW_TOKEN", "").strip()
        model = os.getenv("OPENCLAW_MODEL", "openclaw").strip()
        workspace = Path(os.getenv("KDVOPS_WORKSPACE", "/workspace")).resolve(strict=True)
        cf_access_client_id = os.getenv("CF_ACCESS_CLIENT_ID", "").strip()
        cf_access_client_secret = os.getenv("CF_ACCESS_CLIENT_SECRET", "").strip()
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
        if bool(cf_access_client_id) != bool(cf_access_client_secret):
            raise ValueError(
                "CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET must be configured together"
            )
        return cls(
            endpoint,
            token,
            model,
            workspace,
            cf_access_client_id=cf_access_client_id,
            cf_access_client_secret=cf_access_client_secret,
            allow_sensitive_reads=allow_sensitive_reads,
            allow_sensitive_writes=allow_sensitive_writes,
        )
