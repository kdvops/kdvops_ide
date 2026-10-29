import difflib
import json
import time

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from .agent import AgentClient
from .broker import BrokerConfig, serve
from .config import Settings
from .http_tool import request_https
from .kubectl_tool import kubectl_read
from .workspace import Workspace

app = typer.Typer(help="KDvops CLI: supervised local coding with a remote OpenClaw agent")
console = Console()
MAX_DIFF_CHARS = 16_000
MAX_REPEATED_TOOL_ROUNDS = 5

SYSTEM = """You are a coding assistant controlling a local client through a strict JSON action protocol.
You have no direct filesystem tools. To inspect or modify the mounted workspace, emit exactly one JSON
object per turn, with no Markdown or surrounding text. Request one action at a time using one of:
{"action":"tool","name":"list_files","arguments":{"directory":"."}}
{"action":"tool","name":"list_files","arguments":{"directory":".","recursive":true}}
{"action":"tool","name":"read_file","arguments":{"path":"relative/path"}}
{"action":"tool","name":"write_file","arguments":{"path":"relative/path","content":"complete content"}}
{"action":"tool","name":"http_request","arguments":{"method":"GET","url":"https://dev.azure.com/org/project/_apis/..."}}
{"action":"tool","name":"kubectl_read","arguments":{"args":["get","pods","--all-namespaces","-o","wide"]}}
After receiving each tool result, request the next action or finish with:
{"action":"final","content":"concise explanation of verified work"}
All paths must be relative to the mounted workspace. Never request secrets, absolute paths, '..', shell,
or tools other than those listed above. kubectl_read is strictly for cluster observation and reporting:
never request mutation, Secrets, exec, attach, cp, proxy, port-forward, credential overrides, or interactive
operations. HTTP is HTTPS-only and restricted to locally configured hosts;
non-GET requests and file writes require local human approval. Never claim an operation succeeded until
the corresponding tool result confirms it. Keep edits small and reviewable."""


def load_settings() -> Settings:
    settings = Settings.from_env()
    if not settings.tls_verify:
        console.print(
            "[bold red]DANGER: TLS certificate verification is disabled.[/] "
            "Credentials and traffic can be intercepted."
        )
    return settings


def execute_tool(ws: Workspace, name: str, arguments: dict, settings: Settings | None = None) -> str:
    try:
        if name == "list_files":
            return json.dumps(
                ws.list_files(arguments.get("directory", "."), arguments.get("recursive", False)),
                ensure_ascii=False,
            )
        if name == "read_file":
            return ws.read_file(arguments["path"])
        if name == "write_file":
            path, content = arguments["path"], arguments["content"]
            old = ws.read_file(path) if ws.file_exists(path) else ""
            diff = "".join(difflib.unified_diff(old.splitlines(keepends=True), content.splitlines(keepends=True), fromfile=f"a/{path}", tofile=f"b/{path}"))
            if old == content:
                return "No changes needed"
            if len(diff) > MAX_DIFF_CHARS:
                return (
                    f"ERROR: Diff exceeds display limit ({len(diff)} > {MAX_DIFF_CHARS} characters); "
                    "split the change into smaller edits"
                )
            console.print(Panel(diff or "(No changes)", title=f"Proposed: {path}"))
            if not Confirm.ask(f"Apply modification to {path}?", default=False):
                return "DENIED by user; no changes applied"
            ws.write_file(path, content)
            return f"APPROVED and written: {path}"
        if name == "http_request":
            if settings is None:
                raise ValueError("HTTP settings are unavailable")
            method = str(arguments.get("method", "")).upper()
            url = arguments.get("url", "")
            if method != "GET" and not Confirm.ask(f"Allow {method} request to {url}?", default=False):
                return "DENIED by user; HTTP request not sent"
            return request_https(settings, method, url, arguments.get("json"))
        if name == "kubectl_read":
            return kubectl_read(arguments["args"])
        return "Unknown tool"
    except (ValueError, OSError, UnicodeError, KeyError) as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


def tool_round_fingerprint(calls: list[dict]) -> str:
    """Return a stable fingerprint for detecting an agent stuck on the same tool round."""
    normalized = []
    for call in calls:
        function = call.get("function") or {}
        arguments = function.get("arguments", "")
        try:
            arguments = json.dumps(json.loads(arguments), sort_keys=True, separators=(",", ":"))
        except (json.JSONDecodeError, TypeError):
            arguments = str(arguments)
        normalized.append((function.get("name"), arguments))
    return json.dumps(normalized, separators=(",", ":"))


def check_repeated_tool_round(calls: list[dict], previous: str | None, repeats: int) -> tuple[str, int]:
    fingerprint = tool_round_fingerprint(calls)
    repeats = repeats + 1 if fingerprint == previous else 1
    if repeats >= MAX_REPEATED_TOOL_ROUNDS:
        raise RuntimeError(f"Agent stopped after repeating the same tool round {repeats} times")
    return fingerprint, repeats


def chat_loop(settings: Settings, once: str | None = None) -> None:
    ws = Workspace(
        settings.workspace,
        allow_sensitive_reads=settings.allow_sensitive_reads,
        allow_sensitive_writes=settings.allow_sensitive_writes,
    )
    agent = AgentClient(settings)
    messages = [{"role": "system", "content": SYSTEM}]
    console.print(f"[bold cyan]KDvops CLI[/] | Workspace: {ws.root} | Endpoint: {settings.endpoint}")
    while True:
        if once is not None:
            prompt = once
        else:
            try:
                prompt = console.input("[bold green]kdvops> [/]").strip()
            except (KeyboardInterrupt, EOFError):
                break
        if not prompt:
            if once is not None:
                break
            continue
        if prompt in ("/exit", "/quit"):
            break
        if prompt == "/help":
            console.print("/help /files /exit — All other input is sent to OpenClaw")
            continue
        if prompt == "/files":
            console.print("\n".join(ws.list_files()))
            continue
        messages.append({"role": "user", "content": prompt})
        try:
            deadline = time.monotonic() + settings.run_timeout
            previous_round = None
            repeated_rounds = 0
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Agent run exceeded the {settings.run_timeout:g}-second deadline"
                    )
                reply = agent.complete(messages)
                messages.append(reply)
                calls = reply.get("tool_calls") or []
                if not calls:
                    console.print(Panel(reply.get("content") or "(empty response)", title="OpenClaw"))
                    break
                previous_round, repeated_rounds = check_repeated_tool_round(
                    calls, previous_round, repeated_rounds
                )
                for call in calls:
                    try:
                        args = json.loads(call["function"]["arguments"])
                        result = execute_tool(ws, call["function"]["name"], args, settings)
                    except (KeyError, ValueError, TypeError) as exc:
                        result = f"ERROR: Invalid tool call: {exc}"
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        except (httpx.HTTPError, ValueError, KeyError, TypeError, RuntimeError, TimeoutError) as exc:
            console.print(f"[red]Connection or protocol error:[/] {type(exc).__name__}: {exc}")
            if once is not None:
                raise typer.Exit(1)
        if once is not None:
            break


@app.command()
def chat() -> None:
    """Start interactive coding assistant."""
    try:
        chat_loop(load_settings())
    except (ValueError, OSError) as exc:
        console.print(f"[red]Configuration error:[/] {exc}")
        raise typer.Exit(2)


@app.command()
def run(prompt: str) -> None:
    """Send a single instruction to the agent."""
    try:
        chat_loop(load_settings(), once=prompt)
    except (ValueError, OSError) as exc:
        console.print(f"[red]Configuration error:[/] {exc}")
        raise typer.Exit(2)


@app.command()
def status() -> None:
    """Show configuration without exposing token."""
    try:
        cfg = load_settings()
        console.print(
            f"Endpoint: {cfg.endpoint}\nModel: {cfg.model}\nWorkspace: {cfg.workspace}\n"
            f"Tool iterations: unlimited\nRun deadline: {cfg.run_timeout:g}s\nToken: configured"
        )
    except (ValueError, OSError) as exc:
        console.print(f"[red]Configuration error:[/] {exc}")
        raise typer.Exit(2)


@app.command()
def doctor() -> None:
    """Verify Gateway connectivity and OpenAI-compatible API support."""
    try:
        cfg = load_settings()
        models = AgentClient(cfg).probe()
        console.print("[bold green]Gateway compatible[/]")
        console.print("Available agent targets: " + ", ".join(models), markup=False)
        if cfg.model not in models:
            console.print(f"Configured model {cfg.model!r} is not advertised by the Gateway", markup=False)
    except (ValueError, OSError, httpx.HTTPError) as exc:
        console.print(f"[red]Gateway compatibility check failed:[/] {exc}")
        raise typer.Exit(1)


@app.command()
def broker() -> None:
    """Run the restricted server-side broker for an isolated OpenClaw Gateway."""
    try:
        serve(BrokerConfig.from_env())
    except (ValueError, OSError) as exc:
        console.print(f"[red]Broker configuration error:[/] {exc}")
        raise typer.Exit(2)


@app.command()
def web() -> None:
    """Run the local browser chat interface with persistent conversations."""
    try:
        from .web import serve_web

        serve_web(load_settings())
    except (ValueError, OSError) as exc:
        console.print(f"[red]Web configuration error:[/] {exc}")
        raise typer.Exit(2)


if __name__ == "__main__":
    app()
