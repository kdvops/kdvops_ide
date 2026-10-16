"""OpenAI-compatible Chat Completions client; requires endpoint enabled on OpenClaw."""
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

    def complete(self, messages: list[dict]) -> dict:
        url = self.settings.endpoint + "/chat/completions"
        with httpx.Client(timeout=self.settings.timeout, follow_redirects=False, transport=self.transport) as client:
            response = client.post(url, headers={"Authorization": f"Bearer {self.settings.token}"}, json={"model": self.settings.model, "messages": messages, "tools": TOOLS, "tool_choice": "auto", "stream": False})
            response.raise_for_status()
            payload = response.json()
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Invalid response from OpenClaw: missing choices[0].message") from exc
        if not isinstance(message, dict):
            raise TypeError("Invalid response from OpenClaw: message must be an object")
        return message
