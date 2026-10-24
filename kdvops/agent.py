"""OpenAI-compatible Chat Completions client; requires endpoint enabled on OpenClaw."""
from uuid import uuid4

import httpx

TOOLS = [
    {"type": "function", "function": {"name": "list_files", "description": "List a directory in the mounted workspace", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file from workspace", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "write_file", "description": "Propose writing a UTF-8 text file; local user must approve", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}}},
]


class AgentClient:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport
        self.conversation_id = f"kdvops:{uuid4()}"

    def _client(self) -> httpx.Client:
        return httpx.Client(timeout=self.settings.timeout, follow_redirects=False, transport=self.transport)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.token}",
            "User-Agent": "kdvops-cli/0.2.0",
        }
        client_id = getattr(self.settings, "cf_access_client_id", "")
        client_secret = getattr(self.settings, "cf_access_client_secret", "")
        if client_id and client_secret:
            headers["CF-Access-Client-Id"] = client_id
            headers["CF-Access-Client-Secret"] = client_secret
        return headers

    def probe(self) -> list[str]:
        """Verify that the configured Gateway exposes the OpenAI-compatible API."""
        with self._client() as client:
            response = client.get(
                self.settings.endpoint + "/models",
                headers=self._headers(),
            )
            response.raise_for_status()
            if "application/json" not in response.headers.get("content-type", "").lower():
                raise ValueError(
                    "OpenClaw Chat Completions endpoint appears disabled: /v1/models did not return JSON"
                )
            payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            raise ValueError("Invalid models response from OpenClaw")
        models = [item.get("id") for item in data if isinstance(item, dict)]
        if len(models) != len(data) or not all(isinstance(model, str) and model for model in models):
            raise ValueError("Invalid models response from OpenClaw")
        return models

    def complete(self, messages: list[dict]) -> dict:
        url = self.settings.endpoint + "/chat/completions"
        with self._client() as client:
            response = client.post(url, headers=self._headers(), json={"model": self.settings.model, "user": self.conversation_id, "messages": messages, "tools": TOOLS, "tool_choice": "auto", "stream": False})
            response.raise_for_status()
            payload = response.json()
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Invalid response from OpenClaw: missing choices[0].message") from exc
        if not isinstance(message, dict):
            raise TypeError("Invalid response from OpenClaw: message must be an object")
        return message
