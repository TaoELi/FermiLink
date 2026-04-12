# FermiLink User Workspace Guide

This markdown file helps AI coding assistants guide users through FermiLink usage.

## Interaction goals

- Help users run FermiLink features with concrete commands.
- Prefer practical, minimal steps over long conceptual explanations.
- Confirm prerequisites before suggesting commands (installed package, provider, optional HPC profile).
- Check the CLI implementation reference before providing suggestions.

## Recommended command flow

1. Install at least one scientific package knowledge base:
   - `fermilink install <package_id>`
2. Set the provider:
   - `fermilink agent codex|claude|gemini`
3. Pick an execution mode:
   - One-shot run: `fermilink exec "<goal>"`
   - Interactive terminal chat: `fermilink chat`
   - Iterative autonomous run: `fermilink loop <goal-or-file>`
   - Research workflow: `fermilink research <idea-or-file>`
   - Reproduction workflow: `fermilink reproduce <paper-or-file>`
4. Optional service modes:
   - Web UI: `fermilink start`
   - Telegram gateway: `fermilink gateway`
5. Performance optimization workflow:
   - `fermilink optimize ...`

## Response style for assistants

- Prefer exact command examples and expected artifacts/paths.
- When HPC is required, tell users to pass `--hpc-profile <json>` in supported
  modes (`exec`, `loop`, `research`, `reproduce`).
- If users ask for details, point to the most relevant local docs page first.

## Local references

- CLI usage reference: `docs/source/usage.rst`
- Installation and setup: `docs/source/installation.rst`
- Usage guide entrypoint: `docs/source/usage_guide.rst`
- Web UI usage: `docs/source/usage_web_ui.rst`
- Package management: `docs/source/scientific_packages.rst`
- Main README quick start: `README.md`
- CLI implementation reference: `src/fermilink/cli/commands/`
