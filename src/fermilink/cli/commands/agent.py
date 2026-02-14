from __future__ import annotations

import argparse


def _cli():
    from fermilink import cli

    return cli


def cmd_agent(args: argparse.Namespace) -> int:
    """
    Execute the `agent` CLI subcommand.

    Parameters
    ----------
    args : argparse.Namespace
        Parsed CLI arguments namespace for the subcommand.

    Returns
    -------
    int
        Process exit code (`0` on success, non-zero on failure).
    """
    cli = _cli()
    desired_provider = args.provider
    desired_sandbox_policy: str | None = None
    if args.sandbox and args.bypass_sandbox:
        raise cli.PackageError("Choose only one of --sandbox or --bypass-sandbox.")
    if args.sandbox:
        desired_sandbox_policy = "enforce"
    elif args.bypass_sandbox:
        desired_sandbox_policy = "bypass"

    if desired_provider is None and desired_sandbox_policy is None:
        policy = cli.load_agent_runtime_policy()
        payload = policy.as_dict()
        lines = [
            f"Provider: {policy.provider}.",
            (
                f"Sandbox: enabled ({policy.sandbox_mode})."
                if policy.sandbox_policy == "enforce"
                else "Sandbox: bypassed."
            ),
        ]
        cli._emit_output(args, payload, lines)
        return 0

    updated = cli.save_agent_runtime_policy(
        provider=desired_provider,
        sandbox_policy=desired_sandbox_policy,
    )
    payload = updated.as_dict()
    lines = [
        f"Provider set to {updated.provider}.",
        (
            f"Sandbox enforced with mode {updated.sandbox_mode}."
            if updated.sandbox_policy == "enforce"
            else (
                "Sandbox bypass enabled (Codex internal sandbox only; "
                "external host restrictions may still apply)."
            )
        ),
    ]
    cli._emit_output(args, payload, lines)
    return 0
