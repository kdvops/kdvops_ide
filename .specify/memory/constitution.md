# KDvops CLI Constitution

Version 1.0 · 2026-10-16

1. **Container first:** Python >=3.11; run without host installation other than Docker; `/workspace` is the sole project mount.
2. **Remote reasoning, local execution:** Raspberry Pi OpenClaw provides model/agent; only locally implemented tools access files.
3. **Human control:** All writes are denied by default until an explicit local approval. Never infer approval from model output.
4. **Least privilege:** No privileged Docker, no host Docker socket, no broad host mounts, no arbitrary command execution in MVP.
5. **No secrets in git:** Tokens via environment variables or later secret manager; never log token.
6. **Portable:** Support Windows Docker Desktop and Linux Docker Engine.
7. **Testability:** Unit tests for path traversal, symlinks, approvals and tool handling; mock remote API for CI.
8. **Truthful status:** Distinguish working MVP functionality from unverified integration or roadmap work.
9. **Specification gates:** Update spec, plan and tasks before adding capabilities; only mark tasks done with evidence.
10. **Cloud security:** HTTPS/WSS transport, Gateway auth, optional Cloudflare Access, no unauthenticated public execution tools.
