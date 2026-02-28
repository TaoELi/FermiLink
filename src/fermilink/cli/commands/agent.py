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
    model_unset = object()
    desired_model: str | None | object = model_unset
    if args.sandbox and args.bypass_sandbox:
        raise cli.PackageError("Choose only one of --sandbox or --bypass-sandbox.")
    if args.sandbox:
        desired_sandbox_policy = "enforce"
    elif args.bypass_sandbox:
        desired_sandbox_policy = "bypass"
    if getattr(args, "clear_model", False):
        desired_model = None
    elif isinstance(getattr(args, "model", None), str):
        model_text = str(args.model).strip()
        if not model_text:
            raise cli.PackageError("--model cannot be empty.")
        desired_model = model_text

    if (
        desired_provider is None
        and desired_sandbox_policy is None
        and desired_model is model_unset
    ):
        policy = cli.load_agent_runtime_policy()
        payload = policy.as_dict()
        lines = [
            f"Provider: {policy.provider}.",
            (
                f"Sandbox: enabled ({policy.sandbox_mode})."
                if policy.sandbox_policy == "enforce"
                else "Sandbox: bypassed."
            ),
            (
                f"Model override: {policy.model}."
                if isinstance(policy.model, str) and policy.model
                else "Model override: provider default."
            ),
        ]
        cli._emit_output(args, payload, lines)
        return 0

    save_kwargs: dict[str, object] = {
        "provider": desired_provider,
        "sandbox_policy": desired_sandbox_policy,
    }
    if desired_model is not model_unset:
        save_kwargs["model"] = desired_model
    updated = cli.save_agent_runtime_policy(**save_kwargs)
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
        (
            f"Model override set to {updated.model}."
            if isinstance(updated.model, str) and updated.model
            else "Model override cleared (provider default model selection)."
        ),
    ]
    cli._emit_output(args, payload, lines)
    return 0
