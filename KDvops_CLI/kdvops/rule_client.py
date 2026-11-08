"""Client for the deterministic DevOps rule service.

This adapter lets KDvops operate without OpenClaw. The rule service owns
intent recognition, Kubernetes policy, and execution; this client only
transports the user's latest request and formats its structured response.
"""

import hashlib
import json

import httpx
from .privacy import PrivacyFilter


class RuleAgentClient:
    def __init__(self, settings, conversation_id: str | None = None, transport=None):
        self.settings = settings
        self.session_id = (
            "kdvops-" + hashlib.sha256(conversation_id.encode()).hexdigest()[:32]
            if conversation_id
            else None
        )
        self.transport = transport
        self.privacy = PrivacyFilter(require_scanner=True)

    def complete(self, messages: list[dict]) -> dict:
        prompt = next(
            (message.get("content", "") for message in reversed(messages) if message.get("role") == "user"),
            "",
        )
        if not prompt:
            raise ValueError("A user prompt is required")
        payload = {"message": self.privacy.mask_text(prompt)}
        if self.session_id:
            payload["session_id"] = self.session_id
        with httpx.Client(
            timeout=self.settings.timeout,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            response = client.post(f"{self.settings.rule_endpoint}/api/chat", json=payload)
            response.raise_for_status()
            result = response.json()
        self.session_id = result.get("session_id", self.session_id)
        content = self.privacy.mask_text(result.get("reply") or "(empty response)")
        details = result.get("results") or result.get("items")
        if details:
            content += "\n" + json.dumps(self.privacy.mask(details), ensure_ascii=False, indent=2)
        return {"role": "assistant", "content": content}


def agent_for_settings(settings, conversation_id: str | None = None):
    """Create the configured reasoning backend without requiring OpenClaw in rules mode."""
    if settings.mode == "rules" or (settings.mode == "auto" and not settings.endpoint and not settings.token):
        return RuleAgentClient(settings, conversation_id=conversation_id)
    from .agent import AgentClient

    return AgentClient(settings, conversation_id=conversation_id)
