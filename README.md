# KDvops CLI · MVP 0.1.0

Containerized, human-supervised coding CLI written in Python. The agent runs remotely on OpenClaw (Raspberry Pi 5 behind Cloudflare Tunnel), while local tools operate only on a Docker-mounted `/workspace`.

## Important compatibility requirement

**This MVP uses OpenAI-compatible `POST /v1/chat/completions` with function/tool calling**, NOT the native OpenClaw Gateway WebSocket protocol. Your OpenClaw instance must explicitly enable and support that HTTP API and tool calls; a working web dashboard and Gateway token alone do not prove API availability. Check your installed OpenClaw version and Gateway configuration. If the endpoint rejects requests or doesn't return `tool_calls`, native Gateway integration is still an outstanding task in `specs/002-openclaw-integration`.

Do not expose your token in tickets, logs, screenshots or git. Cloudflare Tunnel provides transport, not authorization. Restrict access using Cloudflare Access if possible; make sure machine clients are supported.

## Quick start

```bash
cp .env.example .env
# Edit HOST_WORKSPACE, OPENCLAW_API_URL, OPENCLAW_TOKEN, OPENCLAW_MODEL
# On Linux, set KDVOPS_UID to the result of: id -u
docker compose build
docker compose run --rm kdvops chat
```

Windows PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
docker compose build
docker compose run --rm kdvops chat
```

Single request:

```bash
docker compose run --rm kdvops run "Explain this project and suggest improvements"
```

Status:

```bash
docker compose run --rm kdvops status
```

## Security and limitations

- Docker bind-mounts the selected host directory as `/workspace:rw`. This is an actual write permission: Docker and the Python process can modify those files; human approval is enforced in application logic.
- The CLI only exposes `list_files`, `read_file`, and approval-gated `write_file` to the remote agent. No arbitrary shell execution or remote host filesystem mounts.
- Common secret-bearing files (`.env*`, private keys, PEM/PFX files, kubeconfig and credentials files) are blocked from both reads and writes by default. Deliberate sessions can opt in separately with `KDVOPS_ALLOW_SENSITIVE_READS=true` or `KDVOPS_ALLOW_SENSITIVE_WRITES=true`; avoid doing so unless required.
- Oversized diffs are rejected instead of being partially displayed. Split large changes into reviewable edits.
- No automatic Git rollback, streaming, terminal execution, multi-file atomic transactions, or native Gateway pairing yet.
- The workspace is not a hardened security boundary against a compromised process. Avoid mounting sensitive folders; use a dedicated low-privilege account and backup/version control.
- Path checks reject traversal and symlinks. Writes use a same-directory temporary file and atomic replacement, but concurrent filesystem races and hostile mounts still require further hardening.
- Files sent for analysis travel to the configured remote AI agent and any models/services it uses.
- Cloudflare Access interactive browser policies may block headless API clients. Configure appropriate service authentication if Access is enabled.
- `OPENCLAW_MODEL` and API endpoint are deployment-dependent; test against your version.

## Tests

```bash
python -m pip install -e '.[dev]'
pytest -q
```

GitHub Actions also runs Ruff, the test suite with an 80% coverage floor, and a container build on every pull request and push to `main`.

## Development workflow

See `.specify/memory/constitution.md` and `specs/*/{spec,plan,tasks}.md`. Work through each task in order and only mark it complete after tests and acceptance checks pass.
