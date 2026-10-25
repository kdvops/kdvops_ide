"""Session-local masking for data sent to remote language models.

Masked values are intentionally never restored from model-controlled output.
The remote model sees every marker, so treating a returned marker as authority
to recover the original value would let prompt injection replay secrets into a
tool call or response.
"""

from __future__ import annotations

import ipaddress
import re
import secrets
from collections.abc import Iterable

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


class PrivacyFilter:
    """Mask sensitive values with opaque, session-scoped reversible tokens."""

    def __init__(self, sensitive_values: Iterable[str] = (), *, enabled: bool = True):
        self.enabled = enabled
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

    def mask(self, value):
        """Return a recursively masked copy of JSON-compatible data."""
        if isinstance(value, str):
            return self.mask_text(value)
        if isinstance(value, list):
            return [self.mask(item) for item in value]
        if isinstance(value, dict):
            return {key: self.mask(item) for key, item in value.items()}
        return value
