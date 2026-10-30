# KDvops CLI · 0.6.0-dev

Containerized, human-supervised coding CLI written in Python. The agent runs remotely on OpenClaw (Raspberry Pi 5 behind Cloudflare Tunnel), while local tools operate only on a Docker-mounted `/workspace`.

## Important compatibility requirement

**This MVP uses OpenAI-compatible `POST /v1/chat/completions` with a constrained JSON action protocol**, NOT the native OpenClaw Gateway WebSocket protocol. The model requests one of three local actions (`list_files`, `read_file`, or approval-gated `write_file`), and the CLI validates and executes it inside the mounted workspace. This works with OpenClaw's Codex/ChatGPT OAuth runtime, which does not transport client-provided function tools.

Your OpenClaw instance must explicitly enable the HTTP API. A working web dashboard and Gateway token alone do not prove API availability. The restricted broker pins JSON-object responses and rejects unsupported request fields.

Do not expose your token in tickets, logs, screenshots or git. Cloudflare Tunnel provides transport, not authorization. Restrict access using Cloudflare Access if possible; make sure machine clients are supported.

## Quick start

```bash
cp .env.example .env
# Edit HOST_WORKSPACE, OPENCLAW_API_URL, OPENCLAW_TOKEN, OPENCLAW_MODEL
# If Cloudflare Access protects the endpoint, also set its Service Token pair.
# On Linux, set KDVOPS_UID to the result of: id -u
docker compose build
docker compose up -d kdvops-runtime
```

The container now runs as a persistent local runtime. It stores sessions and event history in the
`kdvops-runtime-data` volume and exposes its authenticated API only on `127.0.0.1:8765`. The upcoming
lightweight terminal client will use this API; broker, Cloudflare, Git, kubectl, curl, and workspace
credentials remain inside the container.

Implemented runtime endpoints:

- `POST /v1/sessions` and `GET /v1/sessions`
- `POST /v1/sessions/{id}/messages` for queueing while work is active
- `POST /v1/sessions/{id}/stop` for cooperative cancellation
- `GET /v1/sessions/{id}` for persistent history and status
- `GET /v1/sessions/{id}/events?after=<id>` for incremental operation events

All endpoints require `Authorization: Bearer $KDVOPS_RUNTIME_TOKEN`. Mutation approval remains denied
until the client-side approval channel is connected, so the runtime does not silently approve writes.

The image includes `kubectl` and PowerShell 7 (`pwsh`). Set the host kubeconfig path in `.env`; Compose
mounts it read-only and exports the in-container path through `KUBECONFIG`:

```env
HOST_KUBECONFIG=/home/you/.kube/config
```

On Windows Docker Desktop, use a Docker-accessible path such as
`HOST_KUBECONFIG=C:/Users/you/.kube/config`. The remote agent can use the dedicated `kubectl_read` action
to inspect cluster health and produce reports. It supports observation commands such as `get`, `describe`,
`logs`, `top`, `events`, `api-resources`, `cluster-info`, `version`, `explain`, `auth can-i`, and
`rollout status/history`.

The action does not expose a shell. It rejects mutations, Kubernetes Secrets, interactive commands,
`exec`, `attach`, `cp`, `proxy`, `port-forward`, watch/follow modes, external template files, alternate
credentials, and API/server overrides. Commands have a 30-second timeout and bounded output. The kubectl
subprocess receives a minimal environment rather than the OpenClaw, Cloudflare Access, and Azure DevOps
credentials loaded by the main CLI.

For defense in depth, use a dedicated static kubeconfig whose identity is restricted by Kubernetes RBAC
to `get`, `list`, and `watch` only. Kubeconfigs containing `exec`, `auth-provider`, or `proxy-url` are
rejected before every remote query. A read-only kubeconfig makes the API Server enforce the same policy even if a future
client-side validation bug is discovered. Avoid using a cluster-admin kubeconfig for agent sessions.
Exclude `secrets` and `configmaps` from the role, and scope access to only the namespaces and workload
resources required for reporting. Pod specifications and application logs can themselves contain sensitive
literal values, so do not grant access to workloads whose output should not be sent to the configured model.

The image build disables pip certificate validation for the Python package hosts by default so it can run
behind workplace TLS-inspection proxies:

```env
KDVOPS_PIP_TRUSTED_HOST=pypi.org files.pythonhosted.org
```

Rebuild with `docker compose build --no-cache`. This affects dependency downloads during the build only;
it does not disable TLS for the running CLI. `KDVOPS_PIP_TRUSTED_HOST` can be overridden with a
space-separated host list if a different package index is used. TLS for pulling `python:3.12-slim` is
controlled by the Docker daemon and cannot be disabled from this Dockerfile.

Windows PowerShell:

```powershell
Copy-Item .env.example .env
notepad .env
docker compose build
docker compose run --rm --entrypoint kdvops kdvops-runtime chat
```

Single request:

```bash
docker compose run --rm --entrypoint kdvops kdvops-runtime run "Explain this project and suggest improvements"
```

Recursive file inspection and HTTPS APIs are available through constrained local actions. Network access is
disabled by default; enable it only for exact hosts you trust:

```env
KDVOPS_HTTP_ENABLED=true
KDVOPS_HTTP_ALLOWED_HOSTS=dev.azure.com,hooks.example.com
AZURE_DEVOPS_PAT=<local-pat>
```

The PAT is injected only for `https://dev.azure.com` and is never included in the model-visible arguments.
`GET` requests run after URL validation; `POST`, `PUT`, `PATCH`, and `DELETE` require local confirmation.
Only HTTPS port 443 is accepted, redirects are not followed, proxy environment variables are ignored, and
responses are capped at 1 MB. Add webhook hosts explicitly to `KDVOPS_HTTP_ALLOWED_HOSTS`.

Status:

```bash
docker compose run --rm --entrypoint kdvops kdvops-runtime status
```

Gateway compatibility check:

```bash
docker compose run --rm --entrypoint kdvops kdvops-runtime doctor
```

The command verifies authentication, confirms that `/v1/models` returns the OpenClaw agent targets, and detects the common case where the Gateway serves dashboard HTML because Chat Completions is disabled.

For a broker protected by Cloudflare Access, configure the dedicated Service Token alongside the broker token:

```env
CF_ACCESS_CLIENT_ID=<service-token-client-id>
CF_ACCESS_CLIENT_SECRET=<service-token-client-secret>
```

The CLI sends these values as `CF-Access-Client-Id` and `CF-Access-Client-Secret` on both model discovery and chat-completion requests. Both variables must be set together. They are separate from `OPENCLAW_TOKEN`, which authenticates to the broker after Cloudflare permits the request.

`KDVOPS_TIMEOUT` controls the HTTP timeout in seconds and defaults to `300`, allowing enough time for gateway orchestration and cloud model responses.
`KDVOPS_RUN_TIMEOUT` controls the total deadline for one agent run and defaults to `1800` seconds.

Agent/tool rounds are not capped. A request continues until the model returns a response without tool
calls, the total run deadline is reached, a repeated tool cycle is detected, or the user stops the process.

TLS certificate validation is enabled by default. If a workplace TLS-inspection proxy prevents the CLI
from connecting and its CA certificate cannot be installed, it can be disabled explicitly:

```env
KDVOPS_TLS_VERIFY=false
```

Use this only as a temporary workaround on a trusted network. Disabling verification allows a
man-in-the-middle to read the broker and Cloudflare Access credentials and all exchanged content.

For OpenClaw 2026.7.1+, enable the API on a private/controlled ingress:

```json5
{
  gateway: {
    http: {
      endpoints: {
        chatCompletions: { enabled: true }
      }
    }
  }
}
```

Restart the Gateway after changing its configuration. Treat its bearer token as full operator access; do not expose this API publicly without a trusted access boundary.

## Restricted broker

Production clients should connect to the restricted broker instead of receiving the Gateway operator token. The broker exposes only `GET /health`, authenticated `GET /v1/models`, and authenticated `POST /v1/chat/completions`. It pins requests to `openclaw/kdvops`, permits only the JSON-object response format used by the local action protocol, rejects streaming and unknown fields, and applies body/rate limits.

Required server-side environment variables:

```env
KDVOPS_BROKER_TOKEN=<dedicated-client-token>
OPENCLAW_UPSTREAM_TOKEN=<isolated-gateway-token>
OPENCLAW_UPSTREAM_URL=http://openclaw-kdvops:18789/v1
OPENCLAW_UPSTREAM_MODEL=openclaw/kdvops
```

Run it from the published image with `broker` as the command. Never reuse the primary OpenClaw Gateway token.

The broker has been deployed behind public HTTPS ingress and Cloudflare Access and validated against an isolated OpenClaw Gateway in Kubernetes. The CLI receives only the dedicated broker and Cloudflare Service Token credentials; the Gateway operator token remains server-side.

## Security and limitations

- Docker bind-mounts the selected host directory as `/workspace:rw`. This is an actual write permission: Docker and the Python process can modify those files; human approval is enforced in application logic.
- The CLI exposes recursive `list_files`, `read_file`, approval-gated `write_file`, allowlisted `http_request`, and strictly read-only `kubectl_read`. PowerShell remains manual-only. There is no generic shell action, arbitrary `curl`, remote host filesystem mount, Kubernetes Secret access, or cluster mutation action.
- Common secret-bearing files (`.env*`, private keys, PEM/PFX files, kubeconfig and credentials files) are blocked from both reads and writes by default. Deliberate sessions can opt in separately with `KDVOPS_ALLOW_SENSITIVE_READS=true` or `KDVOPS_ALLOW_SENSITIVE_WRITES=true`; avoid doing so unless required.
- Privacy masking is enabled by default. Before every LLM request, the local client replaces known credentials, private keys, JWTs, bearer tokens, secret assignments, email addresses, phone numbers, Azure PAT-shaped values, and private IP addresses with random session-scoped markers. Model output is untrusted, so returned markers are never converted back into sensitive values or expanded in tool arguments. Disable masking only with `KDVOPS_PRIVACY_MASKING=false`; add exact application-specific values with `KDVOPS_PRIVACY_SENSITIVE_VALUES=value1||value2`.
- The masking map is memory-only and one-way from the model's perspective; it dies with the CLI process and is never persisted or sent to the broker. Pattern recognition is defense in depth, not a mathematical guarantee that arbitrary unknown sensitive data will always be recognized. Keep sensitive-file blocking enabled and avoid mounting unnecessary secrets.
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

Pushes to `main` publish a multi-architecture image (`linux/amd64` and `linux/arm64`) to Docker Hub:

```bash
docker pull silencfox/kdvops_cl:latest
```

Every build also receives a commit-specific `sha-<full-commit>` tag. Publishing requires the `DOCKERHUB_TOKEN` GitHub Actions secret; pull requests build and test the image without receiving Docker Hub credentials or pushing it.

## Development workflow

See `.specify/memory/constitution.md` and `specs/*/{spec,plan,tasks}.md`. Work through each task in order and only mark it complete after tests and acceptance checks pass.
