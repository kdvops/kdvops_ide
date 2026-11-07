# Specification: Restricted OpenClaw broker

## User story
As a KDvops user I can connect to an isolated OpenClaw agent without receiving the Gateway operator credential.

## Functional requirements
- FR-01: Authenticate clients with a dedicated broker bearer token.
- FR-02: Keep the upstream Gateway token server-side only.
- FR-03: Expose only health, models and non-streaming chat completions.
- FR-04: Pin all chat requests to `openclaw/kdvops`.
- FR-05: Allow only `list_files`, `read_file` and `write_file` client tools.
- FR-06: Enforce request size and rate limits.

## Acceptance criteria
- Unauthorized calls return HTTP 401.
- Unknown routes and tools are rejected.
- Safe tool-call requests are forwarded with the pinned agent target.
- Logs and errors never include either bearer token or request body.
