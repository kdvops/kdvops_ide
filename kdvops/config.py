import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


INSECURE_TLS_ACKNOWLEDGEMENT = "I_UNDERSTAND_CREDENTIALS_CAN_BE_INTERCEPTED"


@dataclass(frozen=True)
class Settings:
    endpoint: str
    token: str
    model: str
    workspace: Path
    cf_access_client_id: str = ""
    cf_access_client_secret: str = ""
    timeout: float = 300.0
    tls_verify: bool = True
    allow_sensitive_reads: bool = False
    allow_sensitive_writes: bool = False
    http_enabled: bool = False
    http_allowed_hosts: tuple[str, ...] = ()
    azure_devops_pat: str = ""
    privacy_masking: bool = True
    privacy_sensitive_values: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        endpoint = os.getenv("OPENCLAW_API_URL", "").strip().rstrip("/")
        token = os.getenv("OPENCLAW_TOKEN", "").strip()
        model = os.getenv("OPENCLAW_MODEL", "openclaw").strip()
        timeout = float(os.getenv("KDVOPS_TIMEOUT", "300"))
        tls_verify = os.getenv("KDVOPS_TLS_VERIFY", "true").strip().lower() not in {
            "0", "false", "no",
        }
        insecure_tls_ack = os.getenv("KDVOPS_INSECURE_TLS_ACK", "").strip()
        insecure_tls_allowed_hosts = {
            host.strip().lower()
            for host in os.getenv("KDVOPS_INSECURE_TLS_ALLOWED_HOSTS", "").split(",")
            if host.strip()
        }
        workspace = Path(os.getenv("KDVOPS_WORKSPACE", "/workspace")).resolve(strict=True)
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
        if not endpoint.startswith("https://") and not endpoint.startswith("http://127.0.0.1:"):
            raise ValueError("OPENCLAW_API_URL must be HTTPS (or localhost for development)")
        if not token:
            raise ValueError("OPENCLAW_TOKEN is required")
        if timeout <= 0:
            raise ValueError("KDVOPS_TIMEOUT must be greater than zero")
        if bool(cf_access_client_id) != bool(cf_access_client_secret):
            raise ValueError(
                "CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET must be configured together"
            )
        if not tls_verify:
            endpoint_host = (urlsplit(endpoint).hostname or "").lower()
            if insecure_tls_ack != INSECURE_TLS_ACKNOWLEDGEMENT:
                raise ValueError(
                    "Disabling TLS verification requires KDVOPS_INSECURE_TLS_ACK="
                    f"{INSECURE_TLS_ACKNOWLEDGEMENT}"
                )
            if endpoint_host not in insecure_tls_allowed_hosts:
                raise ValueError(
                    "The endpoint hostname must be explicitly listed in "
                    "KDVOPS_INSECURE_TLS_ALLOWED_HOSTS when TLS verification is disabled"
                )
        return cls(
            endpoint,
            token,
            model,
            workspace,
            cf_access_client_id=cf_access_client_id,
            cf_access_client_secret=cf_access_client_secret,
            timeout=timeout,
            tls_verify=tls_verify,
            allow_sensitive_reads=allow_sensitive_reads,
            allow_sensitive_writes=allow_sensitive_writes,
            http_enabled=http_enabled,
            http_allowed_hosts=http_allowed_hosts,
            azure_devops_pat=azure_devops_pat,
            privacy_masking=privacy_masking,
            privacy_sensitive_values=privacy_sensitive_values,
        )
