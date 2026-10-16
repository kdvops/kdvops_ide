# Specification: Mounted workspace operations

## User story
As a developer I can inspect and change only project files under a mounted Docker volume.

## Functional requirements
- FR-01: List and read UTF-8 text files
- FR-02: Write proposed text files after approval
- FR-03: Reject absolute paths, traversal and symlinks
- FR-04: Limit file size and list results

## Acceptance criteria
- Traversal tests pass
- File writes stay inside workspace

## Out of scope
Unapproved host execution, IDE UI, automatic deployment, and broad filesystem access.
