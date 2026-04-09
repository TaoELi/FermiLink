# FermiLink Development Guide

This file defines repository-level development guidance for work in this
FermiLink codebase.

## Core principles

- Implement new behavior with minimal modification to existing code.
- Extend existing modules before introducing new files or abstractions.
- Keep behavior deterministic and failure modes explicit.
- Preserve backward compatibility unless the request explicitly requires change.
- Avoid unrelated refactors while addressing a focused task.

## Development workflow

- Read `project_history.md` before implementation to understand architecture,
  ownership, and prior decisions.
- Prefer the file paths already designated in `project_history.md` for the
  behavior you are changing.
- Keep `web`, `exec`, and `chat` behavior aligned when touching shared routing,
  policy, or overlay logic.
- Add or update tests for behavior changes.
- Run targeted validation first, then broaden only when risk is cross-cutting.

## `project_history.md` maintenance (required)

After any substantive code change, update `project_history.md` in the same
work session:

- Update **Key Implemented Features (Current)** when capability-level behavior
  changes.
- Update **File Scope Map** when file roles change or new maintained paths are
  added.
- Append a dated entry under **Implementation history** summarizing changes in
  concise, factual bullets.
- Keep entries reproducible and avoid speculative wording.

## Boundaries and safety

- Do not revert or rewrite unrelated local changes.
- Do not use destructive git commands unless explicitly requested.
- Ask before broad architecture rewrites or migrations.
