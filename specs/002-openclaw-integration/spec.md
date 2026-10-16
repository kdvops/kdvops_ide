# Specification: Remote OpenClaw connectivity

## User story
As a developer I can send a coding request through Cloudflare Tunnel to OpenClaw on my Raspberry Pi.

## Functional requirements
- FR-01: Token is sent only in Authorization header
- FR-02: HTTP OpenAI-compatible chat endpoint and model are configurable
- FR-03: Function tool calls are processed and results returned
- FR-04: Unsupported Gateway configuration is reported clearly

## Acceptance criteria
- Mock tool call cycle passes
- Live API integration verified against user Gateway (pending)

## Out of scope
Unapproved host execution, IDE UI, automatic deployment, and broad filesystem access.
