# Specification: Container-first Python CLI

## User story
As a developer I can start a Python CLI inside Docker on Windows or Linux without installing an IDE.

## Functional requirements
- FR-01: Typer commands chat/run/status exist
- FR-02: Rich output is readable in a terminal
- FR-03: Python package can be installed and tests run

## Acceptance criteria
- CLI launches with --help
- status does not print secrets

## Out of scope
Unapproved host execution, IDE UI, automatic deployment, and broad filesystem access.
