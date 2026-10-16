import difflib
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm

from .agent import AgentClient
from .config import Settings
from .workspace import Workspace

app = typer.Typer(help="KDvops CLI: supervised local coding with a remote OpenClaw agent")
console = Console()

SYSTEM = """You are a coding assistant. You can inspect and propose edits only through provided tools.
All paths are relative to the mounted workspace. Never request secrets or access outside it.
Write changes require explicit human approval. Do not claim a change was made unless the tool confirms it.
Keep edits small and explain them. There is no shell execution tool."""


def execute_tool(ws: Workspace, name: str, arguments: dict) -> str:
    try:
        if name == "list_files":
            return json.dumps(ws.list_files(arguments.get("directory", ".")), ensure_ascii=False)
        if name == "read_file":
            return ws.read_file(arguments["path"])
        if name == "write_file":
            path, content = arguments["path"], arguments["content"]
            dest = ws.path(path)
            old = ws.read_file(path) if dest.exists() else ""
            diff = "".join(difflib.unified_diff(old.splitlines(keepends=True), content.splitlines(keepends=True), fromfile=f"a/{path}", tofile=f"b/{path}"))
            console.print(Panel(diff[:16000] or "(No changes)", title=f"Proposed: {path}"))
            if old == content:
                return "No changes needed"
            if not Confirm.ask(f"Apply modification to {path}?", default=False):
                return "DENIED by user; no changes applied"
            ws.write_file(path, content)
            return f"APPROVED and written: {path}"
        return "Unknown tool"
    except (ValueError, OSError, UnicodeError, KeyError) as exc:
        return f"ERROR: {type(exc).__name__}: {exc}"


def chat_loop(settings: Settings, once: str | None = None) -> None:
    ws = Workspace(settings.workspace)
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
            for _ in range(12):
                reply = agent.complete(messages)
                messages.append(reply)
                calls = reply.get("tool_calls") or []
                if not calls:
                    console.print(Panel(reply.get("content") or "(empty response)", title="OpenClaw"))
                    break
                for call in calls:
                    try:
                        args = json.loads(call["function"]["arguments"])
                        result = execute_tool(ws, call["function"]["name"], args)
                    except (KeyError, ValueError, TypeError) as exc:
                        result = f"ERROR: Invalid tool call: {exc}"
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            else:
                console.print("[yellow]Tool iteration limit reached[/]")
        except Exception as exc:
            console.print(f"[red]Connection or protocol error:[/] {type(exc).__name__}: {exc}")
            if once is not None:
                raise typer.Exit(1)
        if once is not None:
            break


@app.command()
def chat() -> None:
    """Start interactive coding assistant."""
    try:
        chat_loop(Settings.from_env())
    except (ValueError, OSError) as exc:
        console.print(f"[red]Configuration error:[/] {exc}")
        raise typer.Exit(2)


@app.command()
def run(prompt: str) -> None:
    """Send a single instruction to the agent."""
    try:
        chat_loop(Settings.from_env(), once=prompt)
    except (ValueError, OSError) as exc:
        console.print(f"[red]Configuration error:[/] {exc}")
        raise typer.Exit(2)


@app.command()
def status() -> None:
    """Show configuration without exposing token."""
    try:
        cfg = Settings.from_env()
        console.print(f"Endpoint: {cfg.endpoint}\nModel: {cfg.model}\nWorkspace: {cfg.workspace}\nToken: configured")
    except (ValueError, OSError) as exc:
        console.print(f"[red]Configuration error:[/] {exc}")
        raise typer.Exit(2)


if __name__ == "__main__":
    app()
