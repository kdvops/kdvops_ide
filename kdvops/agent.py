"""OpenAI-compatible client using a constrained JSON protocol for local tools."""
import json
from uuid import uuid4

import httpx

from .privacy import PrivacyFilter

TOOLS = [
    {"type": "function", "function": {"name": "list_files", "description": "List files in the mounted workspace", "parameters": {"type": "object", "properties": {"directory": {"type": "string"}, "recursive": {"type": "boolean"}}, "additionalProperties": False}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a UTF-8 text file from workspace", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "write_file", "description": "Propose writing a UTF-8 text file; local user must approve", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"], "additionalProperties": False}}},
    {"type": "function", "function": {"name": "http_request", "description": "Make an allowlisted HTTPS request; non-GET methods require local approval", "parameters": {"type": "object", "properties": {"method": {"type": "string"}, "url": {"type": "string"}, "json": {"type": ["object", "array", "string", "number", "boolean", "null"]}}, "required": ["method", "url"], "additionalProperties": False}}},
]
TOOL_NAMES = {tool["function"]["name"] for tool in TOOLS}


def decode_protocol_message(message: dict) -> dict:
    """Convert the model's JSON action into the Chat Completions tool-call shape."""
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise TypeError("Invalid KDvops tool protocol response")
    try:
        action = json.loads(message["content"])
    except json.JSONDecodeError:
        # Newer Gateway versions no longer accept response_format. Models may
        # therefore return a normal final answer instead of the requested JSON
        # envelope. Treat non-JSON only as final text; tool execution remains
        # possible exclusively through the strict JSON branch below.
        return {"role": "assistant", "content": message["content"]}
    if not isinstance(action, dict) or action.get("action") not in {"tool", "final"}:
        raise ValueError("Invalid KDvops tool protocol response: unknown action")
    if action["action"] == "final":
        if set(action) != {"action", "content"} or not isinstance(action.get("content"), str):
            raise ValueError("Invalid KDvops final response")
        return {"role": "assistant", "content": action["content"]}

    if set(action) != {"action", "name", "arguments"}:
        raise ValueError("Invalid KDvops tool request")
    name, arguments = action.get("name"), action.get("arguments")
    if name not in TOOL_NAMES or not isinstance(arguments, dict):
        raise ValueError("Invalid or unauthorized KDvops tool request")
    validators = {
        "list_files": lambda args: (
            set(args) <= {"directory", "recursive"}
            and isinstance(args.get("directory", "."), str)
            and isinstance(args.get("recursive", False), bool)
        ),
        "read_file": lambda args: set(args) == {"path"} and isinstance(args.get("path"), str),
        "write_file": lambda args: (
            set(args) == {"path", "content"}
            and isinstance(args.get("path"), str)
            and isinstance(args.get("content"), str)
        ),
        "http_request": lambda args: (
            {"method", "url"} <= set(args) <= {"method", "url", "json"}
            and isinstance(args.get("method"), str)
            and isinstance(args.get("url"), str)
        ),
    }
    if not validators[name](arguments):
        raise ValueError("Invalid KDvops tool arguments")
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"call-{uuid4()}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments, separators=(",", ":"))},
            }
        ],
    }


class AgentClient:
    def __init__(self, settings, transport=None):
        self.settings = settings
        self.transport = transport
        self.conversation_id = f"kdvops:{uuid4()}"
        self.privacy = PrivacyFilter(
            (
                getattr(settings, "token", ""),
                getattr(settings, "cf_access_client_id", ""),
                getattr(settings, "cf_access_client_secret", ""),
                getattr(settings, "azure_devops_pat", ""),
                *getattr(settings, "privacy_sensitive_values", ()),
            ),
            enabled=getattr(settings, "privacy_masking", True),
        )

    def _client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self.settings.timeout,
            follow_redirects=False,
            transport=self.transport,
            verify=getattr(self.settings, "tls_verify", True),
        )

    def _headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.token}",
            "User-Agent": "kdvops-cli/0.4.0",
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
            response = client.post(
                url,
                headers=self._headers(),
                json={
                    "model": self.settings.model,
                    "user": self.conversation_id,
                    "messages": self.privacy.mask(messages),
                    "stream": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError("Invalid response from OpenClaw: missing choices[0].message") from exc
        if not isinstance(message, dict):
            raise TypeError("Invalid response from OpenClaw: message must be an object")
        # Model output is untrusted. In particular, never restore privacy
        # markers returned by the model: it saw those markers in the prompt and
        # could replay one into a final response or a local/HTTP tool call.
        return decode_protocol_message(message)
