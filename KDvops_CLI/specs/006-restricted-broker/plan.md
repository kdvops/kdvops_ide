# Implementation plan: Restricted OpenClaw broker

## Architecture

Place a small ASGI broker between the KDvops CLI and a dedicated OpenClaw Gateway. The CLI receives only a revocable broker credential; the Gateway operator credential remains server-side. The broker exposes a narrow OpenAI-compatible surface, reconstructs allowed tools from local canonical definitions, pins the agent target, and forwards requests without redirects.

## Security controls

- Require distinct high-entropy client and upstream bearer tokens.
- Accept only health, models, and non-streaming chat-completions routes.
- Apply authentication-attempt, authenticated-request, body, response, concurrency, and timeout limits.
- Reject ambiguous framing, duplicate critical headers, unknown fields, embedded upstream credentials, unsafe upstream URLs, and non-canonical tools.
- Run without access logs as a non-root container with a read-only root filesystem, dropped capabilities, and no privilege escalation.

## Verification

1. Unit-test configuration, authentication, canonicalization, framing, limits, and upstream failures.
2. Run Ruff and the full suite with the repository coverage floor.
3. Obtain the mandatory security review before a public push.
4. Deploy beside an isolated Gateway in Kubernetes and complete an end-to-end CLI tool-call test before declaring production readiness.
