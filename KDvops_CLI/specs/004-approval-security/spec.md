# Specification: Human-in-the-loop safety

## User story
As a developer I can review the proposed diff and explicitly accept or reject a write.

## Functional requirements
- FR-01: Default answer is deny
- FR-02: Diff displayed before any write
- FR-03: Rejected changes never touch target file
- FR-04: No shell tool is exposed in MVP

## Acceptance criteria
- Denied write leaves files untouched
- No arbitrary shell execution

## Out of scope
Unapproved host execution, IDE UI, automatic deployment, and broad filesystem access.
