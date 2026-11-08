"""Session-local masking for data sent to remote language models.

Masked values are intentionally never restored from model-controlled output.
The remote model sees every marker, so treating a returned marker as authority
to recover the original value would let prompt injection replay secrets into a
tool call or response.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path

TOKEN_PREFIX = "__KDVOPS_MASK_"
TOKEN_PATTERN = re.compile(r"__KDVOPS_MASK_[A-F0-9]{24}__")

# These deliberately favor precision. Broad guesses create unusable source code
# and make it too easy for a false positive to be restored into a tool call.
PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----", re.DOTALL), 0),
    (re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"), 0),
    (re.compile(r"(?i)(\bBearer\s+)([A-Za-z0-9._~+/=-]{12,})"), 2),
    (re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.IGNORECASE), 0),
    (re.compile(r"(?<!\w)(?:\+?\d[\d .()-]{7,}\d)(?!\w)"), 0),
    (re.compile(r"(?i)((?:password|passwd|pwd|secret|token|api[_-]?key|pat)[\"']?\s*[:=]\s*[\"']?)([^\"'\s&;]{4,})"), 2),
    (re.compile(r"(?<![A-Za-z0-9])(?:[A-Za-z0-9]{52}|[A-Za-z0-9]{75}AZDO[A-Za-z0-9]{4})(?![A-Za-z0-9])"), 0),
)

IPV4_PATTERN = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")


class PrivacyScanError(RuntimeError):
    """Outbound content cannot be released because the scanner is unavailable."""


class PrivacyFilter:
    """Mask sensitive values with opaque, session-scoped reversible tokens."""

    def __init__(
        self,
        sensitive_values: Iterable[str] = (),
        *,
        enabled: bool = True,
        require_scanner: bool = False,
    ):
        self.enabled = enabled
        self.require_scanner = require_scanner
        self._value_to_token: dict[str, str] = {}
        self._known_values = tuple(
            sorted({value for value in sensitive_values if len(value) >= 4}, key=len, reverse=True)
        )

    def _token_for(self, value: str) -> str:
        existing = self._value_to_token.get(value)
        if existing:
            return existing
        while True:
            token = f"{TOKEN_PREFIX}{secrets.token_hex(12).upper()}__"
            if token not in self._value_to_token.values():
                break
        self._value_to_token[value] = token
        return token

    @staticmethod
    def _private_ip(match: re.Match[str]) -> bool:
        try:
            address = ipaddress.ip_address(match.group(0))
        except ValueError:
            return False
        return address.is_private or address.is_loopback or address.is_link_local

    def mask_text(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        if self.require_scanner:
            text = self._mask_with_gitleaks(text)
        # Never interpret user-supplied marker-shaped text as one of our tokens.
        text = TOKEN_PATTERN.sub(lambda match: self._token_for(match.group(0)), text)
        for value in self._known_values:
            text = text.replace(value, self._token_for(value))
        for pattern, sensitive_group in PATTERNS:
            if sensitive_group == 0:
                text = pattern.sub(lambda match: self._token_for(match.group(0)), text)
            else:
                text = pattern.sub(
                    lambda match, group=sensitive_group: match.group(0).replace(
                        match.group(group), self._token_for(match.group(group)), 1
                    ),
                    text,
                )
        text = IPV4_PATTERN.sub(
            lambda match: self._token_for(match.group(0)) if self._private_ip(match) else match.group(0),
            text,
        )
        return text

    def _mask_with_gitleaks(self, text: str) -> str:
        """Redact findings locally before any remote request is made."""
        with tempfile.TemporaryDirectory(prefix="kdvops-outbound-scan-") as directory:
            source = Path(directory) / "payload.txt"
            report = Path(directory) / "report.json"
            source.write_text(text, encoding="utf-8")
            try:
                result = subprocess.run(
                    [
                        "gitleaks", "dir", str(source), "--no-banner", "--config", "/app/gitleaks.toml",
                        "--report-format", "json", "--report-path", str(report), "--exit-code", "1",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    check=False,
                    env={"PATH": os.getenv("PATH", "")},
                )
            except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
                raise PrivacyScanError("Gitleaks no está disponible para filtrar la salida") from exc
            if result.returncode not in {0, 1} or not report.exists():
                raise PrivacyScanError("Gitleaks no pudo completar el filtro de salida")
            try:
                findings = json.loads(report.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                raise PrivacyScanError("Gitleaks devolvió un reporte inválido")
            lines = text.splitlines(keepends=True)
            for finding in sorted(
                (item for item in findings if isinstance(item, dict)),
                key=lambda item: (item.get("StartLine", 0), item.get("StartColumn", 0)),
                reverse=True,
            ):
                line_number = int(finding.get("StartLine") or 0)
                end_line = int(finding.get("EndLine") or line_number)
                if not 1 <= line_number <= len(lines):
                    continue
                marker = self._token_for(f"gitleaks:{finding.get('RuleID', 'secret')}:{line_number}")
                # Redact the complete affected line(s). This is intentionally
                # conservative: column metadata differs between scanner rules,
                # and retaining one trailing character could leak a secret.
                for index in range(max(0, line_number - 1), min(len(lines), end_line)):
                    lines[index] = marker + ("\n" if lines[index].endswith("\n") else "")
            return "".join(lines)

    def mask(self, value):
        """Return a recursively masked copy of JSON-compatible data."""
        if isinstance(value, str):
            return self.mask_text(value)
        if isinstance(value, list):
            return [self.mask(item) for item in value]
        if isinstance(value, dict):
            return {key: self.mask(item) for key, item in value.items()}
        return value
