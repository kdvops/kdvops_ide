# Specification: Docker Compose delivery

## User story
As a developer I can mount a local folder and run the CLI in a restricted container.

## Functional requirements
- FR-01: Bind mount configured through HOST_WORKSPACE
- FR-02: Container uses nonroot user
- FR-03: Read-only root filesystem, dropped capabilities, no published ports
- FR-04: Secrets excluded from Git

## Acceptance criteria
- Compose config parses on Docker host
- CLI can read and write mounted directory after approval

## Out of scope
Unapproved host execution, IDE UI, automatic deployment, and broad filesystem access.
