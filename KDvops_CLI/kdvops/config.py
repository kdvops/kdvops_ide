import math
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
    timeout: float = 300.0
    run_timeout: float = 1800.0
    tls_verify: bool = True
    allow_sensitive_reads: bool = False
    allow_sensitive_writes: bool = False
    http_enabled: bool = False
    http_allowed_hosts: tuple[str, ...] = ()
    azure_devops_pat: str = ""
    privacy_masking: bool = True
    privacy_sensitive_values: tuple[str, ...] = ()
    mode: str = "ai"
    rule_endpoint: str = "http://127.0.0.1:8000"

    @classmethod
    def from_env(cls) -> "Settings":
        mode = os.getenv("KDVOPS_MODE", "ai").strip().lower()
        if mode not in {"ai", "rules", "auto"}:
            raise ValueError("KDVOPS_MODE must be ai, rules, or auto")
        endpoint = os.getenv("OPENCLAW_API_URL", "").strip().rstrip("/")
        token = os.getenv("OPENCLAW_TOKEN", "").strip()
        model = os.getenv("OPENCLAW_MODEL", "openclaw").strip()
        timeout = float(os.getenv("KDVOPS_TIMEOUT", "300"))
        run_timeout = float(os.getenv("KDVOPS_RUN_TIMEOUT", "1800"))
        tls_verify = os.getenv("KDVOPS_TLS_VERIFY", "true").strip().lower() not in {
            "0", "false", "no",
        }
        workspace_default = "." if mode == "rules" else "/workspace"
        workspace = Path(os.getenv("KDVOPS_WORKSPACE", workspace_default)).resolve(strict=True)
        cf_access_client_id = os.getenv("CF_ACCESS_CLIENT_ID", "").strip()
        cf_access_client_secret = os.getenv("CF_ACCESS_CLIENT_SECRET", "").strip()
        allow_sensitive_reads = os.getenv("KDVOPS_ALLOW_SENSITIVE_READS", "").strip().lower() in {
            "1", "true", "yes",
        }
        allow_sensitive_writes = os.getenv("KDVOPS_ALLOW_SENSITIVE_WRITES", "").strip().lower() in {
            "1", "true", "yes",
        }
        http_enabled = os.getenv("KDVOPS_HTTP_ENABLED", "").strip().lower() in {"1", "true", "yes"}
        http_allowed_hosts = tuple(
            host.strip().lower()
            for host in os.getenv("KDVOPS_HTTP_ALLOWED_HOSTS", "dev.azure.com").split(",")
            if host.strip()
        )
        azure_devops_pat = os.getenv("AZURE_DEVOPS_PAT", "").strip()
        privacy_masking = os.getenv("KDVOPS_PRIVACY_MASKING", "true").strip().lower() not in {
            "0", "false", "no",
        }
        privacy_sensitive_values = tuple(
            value for value in os.getenv("KDVOPS_PRIVACY_SENSITIVE_VALUES", "").split("||") if value
        )
        if not workspace.is_dir():
            raise ValueError("Workspace must be a directory")
        rule_endpoint = os.getenv("DEVOPS_RULE_URL", "http://127.0.0.1:8000").strip().rstrip("/")
        if not rule_endpoint.startswith(("http://", "https://")):
            raise ValueError("DEVOPS_RULE_URL must be HTTP or HTTPS")
        ai_configured = bool(endpoint or token)
        if mode == "ai" or (mode == "auto" and ai_configured):
            if not endpoint.startswith("https://") and not endpoint.startswith("http://127.0.0.1:"):
                raise ValueError("OPENCLAW_API_URL must be HTTPS (or localhost for development)")
            if not token:
                raise ValueError("OPENCLAW_TOKEN is required")
        elif mode == "auto" and ai_configured:
            raise ValueError("OPENCLAW_API_URL and OPENCLAW_TOKEN must be configured together")
        if timeout <= 0:
            raise ValueError("KDVOPS_TIMEOUT must be greater than zero")
        if not math.isfinite(run_timeout) or not 0 < run_timeout <= 86_400:
            raise ValueError("KDVOPS_RUN_TIMEOUT must be finite and between 0 and 86400 seconds")
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
            timeout=timeout,
            run_timeout=run_timeout,
            tls_verify=tls_verify,
            allow_sensitive_reads=allow_sensitive_reads,
            allow_sensitive_writes=allow_sensitive_writes,
            http_enabled=http_enabled,
            http_allowed_hosts=http_allowed_hosts,
            azure_devops_pat=azure_devops_pat,
            privacy_masking=privacy_masking,
            privacy_sensitive_values=privacy_sensitive_values,
            mode=mode,
            rule_endpoint=rule_endpoint,
        )
