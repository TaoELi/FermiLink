from __future__ import annotations

from pathlib import Path


def _cli():
    from fermilink import cli

    return cli


def _resolve_compile_tool_source() -> Path:
    return Path(__file__).resolve().parent.parent / "tools" / "sci-skills-generator"


def _run_codex_compile_pass(
    project_root: Path,
    *,
    prompt: str,
    pass_index: int,
    total_passes: int,
    provider: str,
    provider_bin: str,
) -> dict[str, object]:
    cli = _cli()
    sandbox = cli.DEFAULT_COMPILE_SANDBOX
    try:
        cmd = cli.build_exec_command(
            provider=provider,
            provider_bin=provider_bin,
            repo_dir=project_root,
            prompt=prompt,
            sandbox_policy="enforce",
            sandbox_mode=sandbox,
            json_output=False,
        )
    except NotImplementedError as exc:
        raise cli.PackageError(
            f"Compile provider '{provider}' is not implemented yet. "
            "Switch to codex via `fermilink agent codex`."
        ) from exc

    print(f"[compile] pass {pass_index}/{total_passes}: {provider} exec")
    try:
        completed = cli.subprocess.run(cmd, check=False)
    except FileNotFoundError as exc:
        env_key = cli.provider_bin_env_key(provider)
        raise cli.PackageError(
            f"{provider} CLI not found: {provider_bin}. "
            f"Install {provider} or set {env_key}."
        ) from exc

    if completed.returncode != 0:
        raise cli.PackageError(
            f"codex exec failed at compile pass {pass_index}/{total_passes} "
            f"with exit code {completed.returncode}."
        )

    return {
        "pass": pass_index,
        "status": "ok",
        "return_code": completed.returncode,
    }
