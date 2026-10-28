"""Bounded HTTPS client for allowlisted API hosts."""
import base64
import ipaddress
import json
import re
import socket
from urllib.parse import urlsplit

import httpx

ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
MAX_RESPONSE_BYTES = 1_000_000
REDACTIONS = (
    (re.compile(r'(?i)("?(?:access[_-]?token|api[_-]?key|password|secret|pat)"?\s*[:=]\s*")([^"\r\n]+)(")'), r"\1[REDACTED]\3"),
    (re.compile(r"(?i)(authorization\s*:\s*(?:basic|bearer)\s+)[^\s,;]+"), r"\1[REDACTED]"),
)


def _public_address(host: str, resolver=socket.getaddrinfo) -> str:
    addresses = {
        result[4][0]
        for result in resolver(host, 443, type=socket.SOCK_STREAM)
    }
    if not addresses:
        raise ValueError("HTTP host did not resolve")
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            raise ValueError("HTTP host resolves to a non-public address")
    # Pin the request to one of the validated addresses. This prevents a second
    # DNS lookup by the transport and therefore closes the rebinding window.
    return min(addresses)


def _redact(text: str, sensitive_values=()) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    # APIs and webhook endpoints can echo request data under arbitrary keys or
    # as plain text. Redact the configured credential values themselves rather
    # than relying only on field-name patterns.
    for value in sorted({value for value in sensitive_values if value}, key=len, reverse=True):
        text = text.replace(value, "[REDACTED]")
    return text


def request_https(settings, method: str, url: str, payload=None, transport=None, resolver=socket.getaddrinfo) -> str:
    if not settings.http_enabled:
        raise ValueError("HTTP requests are disabled; set KDVOPS_HTTP_ENABLED=true")
    method = method.upper()
    if method not in ALLOWED_METHODS:
        raise ValueError("HTTP method is not allowed")
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or parsed.username or parsed.password or not host:
        raise ValueError("Only credential-free HTTPS URLs are allowed")
    if parsed.port not in {None, 443}:
        raise ValueError("Only HTTPS port 443 is allowed")
    if host not in settings.http_allowed_hosts:
        raise ValueError("HTTP host is not allowlisted")

    address = _public_address(host, resolver)
    authority = f"[{address}]" if ":" in address else address
    pinned_url = parsed._replace(netloc=authority).geturl()

    headers = {
        "Accept": "application/json",
        "Host": host,
        "User-Agent": "kdvops-cli/0.5.0",
    }
    if host == "dev.azure.com" and settings.azure_devops_pat:
        credential = base64.b64encode(f":{settings.azure_devops_pat}".encode()).decode()
        headers["Authorization"] = f"Basic {credential}"
    body = bytearray()
    with (
        httpx.Client(timeout=30, follow_redirects=False, trust_env=False, transport=transport) as client,
        client.stream(
            method,
            pinned_url,
            headers=headers,
            json=payload if method != "GET" else None,
            extensions={"sni_hostname": host},
        ) as response,
    ):
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").lower()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ValueError("HTTP response exceeds size limit")
    if "json" in content_type:
        text = json.dumps(json.loads(body), ensure_ascii=False, separators=(",", ":"))
    else:
        text = bytes(body).decode("utf-8", errors="replace")
    sensitive_values = {
        settings.token,
        settings.cf_access_client_id,
        settings.cf_access_client_secret,
        settings.azure_devops_pat,
    }
    if settings.azure_devops_pat:
        sensitive_values.add(base64.b64encode(f":{settings.azure_devops_pat}".encode()).decode())
    return _redact(text, sensitive_values)
