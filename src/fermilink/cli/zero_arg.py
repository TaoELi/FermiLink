from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import textwrap
import urllib.error

from fermilink.config import resolve_fermilink_home
from fermilink.packages.curated_channels import ChannelPackage


def _cli():
    from fermilink import cli

    return cli


_ZERO_ARG_MENU_CHOICES = (
    ("1", "Continue setup", "continue_setup"),
    ("2", "Run a simulation", "run_simulation"),
    ("3", "Install scientific packages", "install_packages"),
    ("4", "Open web UI", "open_web_ui"),
    ("5", "Set up Telegram", "setup_telegram"),
    ("6", "Configure HPC", "configure_hpc"),
    ("7", "Advanced: Compile a local package for FermiLink", "compile_package"),
    (
        "8",
        "Advanced: Update package skills with research pipelines / memory",
        "recompile_package",
    ),
    ("9", "Show system status", "show_status"),
    ("10", "Quit", "quit"),
)
_ZERO_ARG_DEFAULT_WORKSPACE_NAME = "fermilink-workspace"
_ZERO_ARG_DEFAULT_HPC_PROFILE_ENV = "FERMILINK_DEFAULT_HPC_PROFILE"
_ZERO_ARG_DEFAULT_HPC_FILENAME = "HPC_PROFILE.json"
_ZERO_ARG_LEGACY_HPC_FILENAME = "hpc_profile.json"
_ZERO_ARG_CODEX_AUTH_MODES = {"login", "oauth", "keychain", "stored"}
_ZERO_ARG_CODEX_PLACEHOLDER_KEYS = {
    "YOUR_KEY_HERE",
    "YOUR_REAL_OPENAI_API_KEY",
    "YOUR_KEY*HERE",
    "CHANGEME",
}
_ZERO_ARG_HERO_LINES = (
    "##########################################################################",
    "#                                                                        #",
    "#   FFFFF  EEEEE  RRRR   M   M  IIII        L     IIII  N   N  K   K     #",
    "#   F      E      R   R  MM MM   II         L      II   NN  N  K  K      #",
    "#   FFFF   EEEE   RRRR   M M M   II         L      II   N N N  KKK       #",
    "#   F      E      R R    M   M   II         L      II   N  NN  K  K      #",
    "#   F      EEEEE  R  RR  M   M  IIII        LLLLL IIII  N   N  K   K     #",
    "#                                                                        #",
    "#      Entry point for interactive autonomous scientific simulations     #",
    "#                                                                        #",
    "##########################################################################",
)
_ZERO_ARG_TABLE_WIDTHS = (10, 14, 44)


@contextlib.contextmanager
def _pushd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _effective_argv(argv: list[str] | None) -> list[str]:
    if argv is None:
        return list(sys.argv[1:])
    return list(argv)


def _interactive_tty() -> bool:
    return bool(sys.stdin.isatty() and sys.stdout.isatty())


def _execute_cli_argv(argv: list[str]) -> int:
    cli = _cli()
    parser = cli._build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 1

    try:
        return args.func(args)
    except urllib.error.URLError as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 2
    except (
        cli.PackageError,
        cli.PackageNotFoundError,
        cli.PackageValidationError,
        ValueError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2


def _resolve_provider_binary_for_probe(provider: str) -> tuple[str, str | None]:
    cli = _cli()
    env_key = cli.provider_bin_env_key(provider)
    raw_binary = cli.resolve_provider_binary(
        provider,
        provider_bin_override=os.getenv(env_key),
    )
    parts = shlex.split(raw_binary)
    executable = parts[0] if parts else raw_binary
    return raw_binary, shutil.which(executable)


def _probe_provider_auth_state(provider: str, *, binary_found: bool) -> str:
    if not binary_found:
        return "missing"
    if provider != "codex":
        return "unknown"

    auth_mode = (
        (os.getenv("FERMILINK_CODEX_AUTH_MODE") or os.getenv("CODEX_AUTH_MODE") or "")
        .strip()
        .lower()
    )
    if auth_mode in _ZERO_ARG_CODEX_AUTH_MODES:
        return "ready"

    key = (
        os.getenv("FERMILINK_CODEX_API_KEY")
        or os.getenv("FERMILINK_OPENAI_API_KEY")
        or os.getenv("CODEX_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or ""
    ).strip()
    if key and key not in _ZERO_ARG_CODEX_PLACEHOLDER_KEYS:
        return "ready"
    return "unknown"


def _validate_zero_arg_hpc_profile(path: Path) -> tuple[bool, str | None]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return False, str(exc)
    except json.JSONDecodeError as exc:
        return False, f"invalid JSON: {exc}"

    if not isinstance(payload, dict):
        return False, "expected top-level JSON object"

    required = (
        "slurm_default_partition",
        "slurm_defaults",
        "slurm_resource_policy",
    )
    missing = [key for key in required if not str(payload.get(key) or "").strip()]
    if missing:
        return False, "missing keys: " + ", ".join(missing)
    return True, None


def _probe_zero_arg_hpc_state() -> dict[str, object]:
    cli = _cli()
    candidates: list[Path] = []
    raw_env = os.getenv(_ZERO_ARG_DEFAULT_HPC_PROFILE_ENV)
    if isinstance(raw_env, str) and raw_env.strip():
        candidates.append(cli._resolve_project_path(raw_env.strip()))
    candidates.append((Path.cwd() / _ZERO_ARG_DEFAULT_HPC_FILENAME).resolve())
    candidates.append(resolve_fermilink_home() / _ZERO_ARG_DEFAULT_HPC_FILENAME)
    candidates.append((Path.cwd() / _ZERO_ARG_LEGACY_HPC_FILENAME).resolve())
    candidates.append(resolve_fermilink_home() / _ZERO_ARG_LEGACY_HPC_FILENAME)

    seen: set[str] = set()
    chosen_path: Path | None = None
    valid = False
    error: str | None = None
    for candidate in candidates:
        token = str(candidate)
        if token in seen:
            continue
        seen.add(token)
        if not candidate.is_file():
            continue
        chosen_path = candidate
        valid, error = _validate_zero_arg_hpc_profile(candidate)
        break

    return {
        "profile_path": str(chosen_path) if isinstance(chosen_path, Path) else None,
        "profile_valid": valid,
        "profile_error": error,
        "slurm_submit_available": shutil.which("sbatch") is not None,
        "slurm_wait_available": (
            shutil.which("squeue") is not None or shutil.which("sacct") is not None
        ),
    }


def _probe_zero_arg_state() -> dict[str, object]:
    cli = _cli()
    runtime_policy = cli.load_agent_runtime_policy()
    provider_scan: dict[str, dict[str, object]] = {}
    for provider in cli.SUPPORTED_PROVIDERS:
        requested_binary, binary_path = _resolve_provider_binary_for_probe(provider)
        binary_found = binary_path is not None
        provider_scan[provider] = {
            "provider": provider,
            "requested_binary": requested_binary,
            "binary_path": binary_path,
            "binary_found": binary_found,
            "auth_state": _probe_provider_auth_state(
                provider, binary_found=binary_found
            ),
        }

    selected_provider: str | None = None
    preferred = provider_scan.get(runtime_policy.provider)
    if isinstance(preferred, dict) and preferred.get("binary_found"):
        selected_provider = runtime_policy.provider
    else:
        for provider in cli.SUPPORTED_PROVIDERS:
            candidate = provider_scan.get(provider)
            if isinstance(candidate, dict) and candidate.get("binary_found"):
                selected_provider = provider
                break

    scipkg_root = cli.resolve_scipkg_root()
    registry = cli.load_registry(scipkg_root)
    package_count = cli._installed_package_count(registry)
    active_package = registry.get("active_package")
    if not isinstance(active_package, str) or not active_package.strip():
        active_package = None

    runtime_root = cli.resolve_runtime_root()
    runner_status = cli.service_status(runtime_root, "runner")
    web_status = cli.service_status(runtime_root, "web")

    return {
        "runtime_policy": runtime_policy,
        "provider_scan": provider_scan,
        "selected_provider": selected_provider,
        "provider_setup_needed": (
            selected_provider is None or selected_provider != runtime_policy.provider
        ),
        "packages": {
            "count": package_count,
            "has_packages": package_count > 0,
            "active_package": active_package,
            "scipkg_root": str(scipkg_root),
        },
        "services": {
            "runner": runner_status,
            "web": web_status,
        },
        "telegram": {
            "token_present": bool(
                str(os.getenv("FERMILINK_GATEWAY_TELEGRAM_TOKEN") or "").strip()
            ),
            "allowlist_present": bool(
                str(os.getenv("FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM") or "").strip()
            ),
        },
        "hpc": _probe_zero_arg_hpc_state(),
    }


def _provider_scan_line(state: dict[str, object]) -> str:
    cli = _cli()
    runtime_policy = state["runtime_policy"]
    selected_provider = state.get("selected_provider")
    provider_scan = state["provider_scan"]
    rendered: list[str] = []
    for provider in cli.SUPPORTED_PROVIDERS:
        item = provider_scan.get(provider, {})
        if item.get("binary_found"):
            auth_state = str(item.get("auth_state") or "unknown")
            suffix = "auth ready" if auth_state == "ready" else "auth unverified"
            rendered.append(f"{provider} ({suffix})")
        else:
            rendered.append(f"{provider} (missing)")
    selected_text = selected_provider or "none"
    return (
        f"Providers: default={runtime_policy.provider}, selected={selected_text}; "
        + ", ".join(rendered)
        + "."
    )


def _style_zero_arg_banner_line(line: str) -> str:
    return _cli()._style_text(line, "1", "38;5;45")


def _print_zero_arg_hero_banner() -> None:
    for line in _ZERO_ARG_HERO_LINES:
        print(_style_zero_arg_banner_line(line))
    print()


def _wrap_zero_arg_table_cell(text: object, *, width: int) -> list[str]:
    normalized = " ".join(str(text or "").split()).strip()
    if not normalized:
        normalized = "-"
    return textwrap.wrap(normalized, width=width) or ["-"]


def _render_zero_arg_table(
    title: str,
    headers: tuple[str, str, str],
    rows: list[tuple[str, str, str]],
) -> list[str]:
    widths = _ZERO_ARG_TABLE_WIDTHS
    border = "+" + "+".join("-" * (width + 2) for width in widths) + "+"
    title_inner_width = len(border) - 4
    rendered = [border, f"| {title:<{title_inner_width}} |", border]
    rendered.append(
        f"| {headers[0]:<{widths[0]}} | {headers[1]:<{widths[1]}} | {headers[2]:<{widths[2]}} |"
    )
    rendered.append(border)
    for component, status, details in rows:
        component_lines = _wrap_zero_arg_table_cell(component, width=widths[0])
        status_lines = _wrap_zero_arg_table_cell(status, width=widths[1])
        detail_lines = _wrap_zero_arg_table_cell(details, width=widths[2])
        line_count = max(len(component_lines), len(status_lines), len(detail_lines))
        for index in range(line_count):
            rendered.append(
                "| "
                f"{component_lines[index] if index < len(component_lines) else '':<{widths[0]}}"
                " | "
                f"{status_lines[index] if index < len(status_lines) else '':<{widths[1]}}"
                " | "
                f"{detail_lines[index] if index < len(detail_lines) else '':<{widths[2]}}"
                " |"
            )
        rendered.append(border)
    return rendered


def _zero_arg_provider_status_row(state: dict[str, object]) -> tuple[str, str, str]:
    cli = _cli()
    runtime_policy = state["runtime_policy"]
    selected_provider = state.get("selected_provider")
    provider_scan = state["provider_scan"]
    if isinstance(selected_provider, str):
        auth_state = provider_scan.get(selected_provider, {}).get("auth_state")
        status = (
            f"selected={selected_provider}"
            if auth_state == "ready"
            else f"{selected_provider} pending"
        )
    else:
        status = "not configured"
    detected: list[str] = []
    for provider in cli.SUPPORTED_PROVIDERS:
        item = provider_scan.get(provider, {})
        if item.get("binary_found"):
            detected.append(provider)
    rendered: list[str] = [
        f"default={runtime_policy.provider}",
        f"detected={', '.join(detected) if detected else 'none'}",
    ]
    return ("Providers", status, "; ".join(rendered))


def _zero_arg_packages_status_row(state: dict[str, object]) -> tuple[str, str, str]:
    packages = state["packages"]
    count = int(packages.get("count") or 0)
    active_package = packages.get("active_package")
    status = f"{count} installed" if count > 0 else "not installed"
    details = (
        f"active={active_package}"
        if isinstance(active_package, str) and active_package
        else "active=none"
    )
    return ("Packages", status, details)


def _zero_arg_services_status_row(state: dict[str, object]) -> tuple[str, str, str]:
    services = state["services"]
    runner_running = bool(services["runner"].get("running"))
    web_running = bool(services["web"].get("running"))
    if runner_running and web_running:
        status = "running"
    elif runner_running or web_running:
        status = "partial"
    else:
        status = "stopped"
    details = (
        f"runner={'running' if runner_running else 'stopped'}; "
        f"web={'running' if web_running else 'stopped'}"
    )
    return ("Web UI", status, details)


def _zero_arg_telegram_status_row(state: dict[str, object]) -> tuple[str, str, str]:
    telegram = state["telegram"]
    token_present = bool(telegram["token_present"])
    allowlist_present = bool(telegram["allowlist_present"])
    if token_present and allowlist_present:
        status = "configured"
    elif token_present:
        status = "partial"
    else:
        status = "missing"
    details = (
        f"token={'yes' if token_present else 'no'}; "
        f"allowlist={'yes' if allowlist_present else 'no'}"
    )
    return ("Telegram", status, details)


def _zero_arg_hpc_status_row(state: dict[str, object]) -> tuple[str, str, str]:
    hpc = state["hpc"]
    if hpc["profile_valid"]:
        status = "ready"
        details = (
            f"profile={hpc['profile_path']}; "
            f"sbatch={'yes' if hpc['slurm_submit_available'] else 'no'}; "
            f"wait tools={'yes' if hpc['slurm_wait_available'] else 'no'}"
        )
    elif isinstance(hpc.get("profile_path"), str):
        status = "invalid"
        details = (
            f"profile={hpc['profile_path']}; error={hpc['profile_error']}; "
            f"sbatch={'yes' if hpc['slurm_submit_available'] else 'no'}"
        )
    else:
        status = "not configured"
        details = (
            f"sbatch={'yes' if hpc['slurm_submit_available'] else 'no'}; "
            f"wait tools={'yes' if hpc['slurm_wait_available'] else 'no'}"
        )
    return ("HPC", status, details)


def _print_zero_arg_status_summary(
    state: dict[str, object], *, include_help_hint: bool
) -> None:
    rows = [
        _zero_arg_provider_status_row(state),
        _zero_arg_packages_status_row(state),
        _zero_arg_services_status_row(state),
        _zero_arg_telegram_status_row(state),
        _zero_arg_hpc_status_row(state),
    ]
    for line in _render_zero_arg_table(
        "FermiLink Status",
        ("Component", "State", "Details"),
        rows,
    ):
        print(line)
    if include_help_hint:
        print(
            "Run in an interactive terminal for guided setup, or use `fermilink --help`."
        )


def _prompt_line(prompt: str) -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""


def _prompt_confirm(prompt: str, *, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    answer = _prompt_line(f"{prompt} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in {"y", "yes"}


def _prompt_menu_choice() -> str:
    for key, label, _value in _ZERO_ARG_MENU_CHOICES:
        print(f"{key}. {label}")
    answer = _prompt_line("Choose an option [1-10]: ").strip()
    if not answer:
        return "continue_setup"
    for key, _label, value in _ZERO_ARG_MENU_CHOICES:
        if answer == key:
            return value
    return ""


def _render_zero_arg_command(argv: list[str]) -> str:
    return "fermilink " + shlex.join(argv)


def _normalize_zero_arg_package_id(raw_value: str) -> str | None:
    normalized = _cli().normalize_package_id(raw_value)
    if not normalized:
        print("Package id is required.")
        return None
    return normalized


def _resolve_existing_zero_arg_dir(raw_value: str, *, label: str) -> Path | None:
    cli = _cli()
    resolved = cli._resolve_project_path(raw_value)
    if not resolved.exists():
        print(f"{label} does not exist: {resolved}")
        return None
    if not resolved.is_dir():
        print(f"{label} must be a directory: {resolved}")
        return None
    return resolved


def _resolve_existing_zero_arg_file(raw_value: str, *, label: str) -> Path | None:
    cli = _cli()
    resolved = cli._resolve_project_path(raw_value)
    if not resolved.exists():
        print(f"{label} does not exist: {resolved}")
        return None
    if not resolved.is_file():
        print(f"{label} must be a file: {resolved}")
        return None
    return resolved


def _resolve_existing_zero_arg_file_or_dir(
    raw_value: str, *, label: str
) -> Path | None:
    cli = _cli()
    resolved = cli._resolve_project_path(raw_value)
    if not resolved.exists():
        print(f"{label} does not exist: {resolved}")
        return None
    if not resolved.is_file() and not resolved.is_dir():
        print(f"{label} must be a file or directory: {resolved}")
        return None
    return resolved


def _login_command_for_provider(provider: str) -> str:
    if provider == "codex":
        return "codex login"
    if provider == "claude":
        return "claude login"
    if provider == "gemini":
        return "gemini login"
    if provider == "opencode":
        return "opencode auth login"
    return f"{provider} login"


def _run_zero_arg_provider_setup(state: dict[str, object]) -> None:
    cli = _cli()
    provider_scan = state["provider_scan"]
    available = [
        provider
        for provider in cli.SUPPORTED_PROVIDERS
        if provider_scan.get(provider, {}).get("binary_found")
    ]
    if not available:
        print("No supported provider CLI was detected on PATH.")
        print("Install one supported provider first:")
        print("- Codex: `brew install codex` on macOS or `npm i -g @openai/codex`")
        print("- Claude: install the Claude CLI, then run `claude login`")
        print("- Gemini: install the Gemini CLI, then run `gemini login`")
        print("- OpenCode: install OpenCode, then run `opencode auth login`")
        return

    recommended = state.get("selected_provider") or available[0]
    print("Available providers:")
    for index, provider in enumerate(available, start=1):
        item = provider_scan.get(provider, {})
        auth_state = str(item.get("auth_state") or "unknown")
        label = "auth ready" if auth_state == "ready" else "auth unverified"
        marker = " (recommended)" if provider == recommended else ""
        print(f"{index}. {provider} [{label}]{marker}")

    answer = _prompt_line(
        f"Select provider [1-{len(available)}] (Enter for {recommended}): "
    ).strip()
    if not answer:
        chosen = str(recommended)
    else:
        try:
            chosen = available[int(answer) - 1]
        except (ValueError, IndexError):
            print("Invalid provider selection.")
            return

    updated = cli.save_agent_runtime_policy(provider=chosen)
    print(f"Provider set to {updated.provider}.")
    selected_auth = provider_scan.get(chosen, {}).get("auth_state")
    if selected_auth != "ready":
        print(
            "Login could not be verified automatically. "
            f"If needed, run `{_login_command_for_provider(chosen)}` before the first agent task."
        )


def _rank_curated_matches(query: str) -> list[ChannelPackage]:
    cli = _cli()
    normalized_query = cli.normalize_package_id(query)
    packages = list(cli.list_curated_packages().values())
    exact: list[ChannelPackage] = []
    prefix: list[ChannelPackage] = []
    contains: list[ChannelPackage] = []
    for item in packages:
        package_id = item.package_id.strip().lower()
        title = (item.title or "").strip().lower()
        description = (item.description or "").strip().lower()
        haystack = f"{package_id} {title} {description}"
        if package_id == normalized_query:
            exact.append(item)
        elif package_id.startswith(normalized_query) or title.startswith(query.lower()):
            prefix.append(item)
        elif normalized_query in haystack:
            contains.append(item)
    return exact + prefix + contains


def _run_zero_arg_package_setup(state: dict[str, object]) -> None:
    packages = state["packages"]
    default_hint = (
        "Type a package id or search term"
        + (
            ""
            if packages["has_packages"]
            else " (for example: maxwelllink, meep, qutip)"
        )
        + ": "
    )
    query = _prompt_line(default_hint).strip()
    if not query:
        print("Package setup skipped.")
        return

    matches = _rank_curated_matches(query)
    if not matches:
        print("No curated package match was found.")
        print(
            "If your package is local or unpublished, use `fermilink compile` or `fermilink recompile` later with an explicit project path."
        )
        return

    limited = matches[:10]
    if len(limited) == 1:
        chosen = limited[0]
    else:
        print("Curated package matches:")
        for index, item in enumerate(limited, start=1):
            description = (
                f" - {item.description}"
                if isinstance(item.description, str) and item.description.strip()
                else ""
            )
            print(f"{index}. {item.package_id}: {item.title}{description}")
        answer = _prompt_line(
            f"Select package [1-{len(limited)}] (Enter for 1): "
        ).strip()
        if not answer:
            chosen = limited[0]
        else:
            try:
                chosen = limited[int(answer) - 1]
            except (ValueError, IndexError):
                print("Invalid package selection.")
                return

    activate = _prompt_confirm(
        f"Install `{chosen.package_id}` and make it the active package?",
        default=True,
    )
    argv = ["install", chosen.package_id]
    if activate:
        argv.append("--activate")
    code = _execute_cli_argv(argv)
    if code != 0:
        print(f"Package install failed with exit code {code}.", file=sys.stderr)


def _default_shell_rc_path() -> Path:
    shell_name = Path(str(os.getenv("SHELL") or "")).name
    if shell_name == "bash":
        return Path.home() / ".bashrc"
    return Path.home() / ".zshrc"


def _append_shell_exports(export_lines: list[str]) -> Path:
    rc_path = _default_shell_rc_path()
    existing = ""
    if rc_path.exists():
        existing = rc_path.read_text(encoding="utf-8", errors="replace")
    additions = [line for line in export_lines if line not in existing]
    if additions:
        with rc_path.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            for line in additions:
                handle.write(line + "\n")
    return rc_path


def _run_zero_arg_telegram_setup(state: dict[str, object]) -> int | None:
    telegram = state["telegram"]
    token = str(os.getenv("FERMILINK_GATEWAY_TELEGRAM_TOKEN") or "").strip()
    allow_from = str(os.getenv("FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM") or "").strip()

    if not token:
        print("Telegram setup requires a bot token from @BotFather.")
        token = _prompt_line(
            "Paste FERMILINK_GATEWAY_TELEGRAM_TOKEN (blank to cancel): "
        ).strip()
        if not token:
            print("Telegram setup skipped.")
            return None

    if not allow_from:
        allow_from = _prompt_line(
            "Paste FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM (blank to allow all senders): "
        ).strip()

    os.environ["FERMILINK_GATEWAY_TELEGRAM_TOKEN"] = token
    if allow_from:
        os.environ["FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM"] = allow_from

    if _prompt_confirm(
        "Persist Telegram settings to your shell rc file?", default=False
    ):
        export_lines = [f'export FERMILINK_GATEWAY_TELEGRAM_TOKEN="{token}"']
        if allow_from:
            export_lines.append(
                f'export FERMILINK_GATEWAY_TELEGRAM_ALLOW_FROM="{allow_from}"'
            )
        rc_path = _append_shell_exports(export_lines)
        print(f"Saved Telegram exports to {rc_path}.")

    if not _prompt_confirm("Launch `fermilink gateway` now?", default=False):
        if not telegram["token_present"]:
            print("Telegram configuration recorded for this session.")
        return None

    refreshed = _probe_zero_arg_state()
    if refreshed.get("selected_provider") is None:
        print("A provider CLI is still required before launching the Telegram gateway.")
        return None
    if not _confirm_provider_login_if_needed(refreshed):
        return None

    argv = ["gateway", "--telegram-token", token]
    if allow_from:
        for item in [part.strip() for part in allow_from.split(",") if part.strip()]:
            argv.extend(["--allow-from", item])
    hpc_state = refreshed["hpc"]
    if hpc_state["profile_valid"] and _prompt_confirm(
        f"Use default HPC profile {hpc_state['profile_path']} for gateway-triggered runs?",
        default=False,
    ):
        argv.extend(["--hpc-profile", str(hpc_state["profile_path"])])
    return _execute_cli_argv(argv)


def _run_zero_arg_hpc_setup(state: dict[str, object]) -> None:
    hpc = state["hpc"]
    target_path = resolve_fermilink_home() / _ZERO_ARG_DEFAULT_HPC_FILENAME
    if hpc["profile_valid"]:
        print(f"Current default HPC profile: {hpc['profile_path']}")
        if not _prompt_confirm("Overwrite the default HPC profile?", default=False):
            return

    partition = _prompt_line("SLURM default partition [shared]: ").strip() or "shared"
    slurm_defaults = (
        _prompt_line(
            "SLURM defaults [--nodes=1 --ntasks=1 --ntasks-per-node=1 --cpus-per-task=1 --time=24:00:00]: "
        ).strip()
        or "--nodes=1 --ntasks=1 --ntasks-per-node=1 --cpus-per-task=1 --time=24:00:00"
    )
    resource_policy = (
        _prompt_line(
            "SLURM resource policy [Use serial/single-node defaults unless the method explicitly requires MPI or multi-node scaling]: "
        ).strip()
        or "Use serial/single-node defaults unless the method explicitly requires MPI or multi-node scaling"
    )
    payload = {
        "slurm_default_partition": partition,
        "slurm_defaults": slurm_defaults,
        "slurm_resource_policy": resource_policy,
    }
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Saved default HPC profile to {target_path}.")
    if not hpc["slurm_submit_available"]:
        print("Warning: `sbatch` was not detected on this machine.")
    elif not hpc["slurm_wait_available"]:
        print(
            "Warning: `squeue`/`sacct` were not detected; job wait polling may be limited."
        )


def _infer_zero_arg_mode(prompt: str) -> str:
    lowered = prompt.strip().lower()
    reproduce_markers = (
        "reproduce",
        "replicate",
        "figure",
        "table",
        "paper",
        "manuscript",
        "arxiv",
        ".pdf",
        ".tex",
        "doi",
    )
    if any(marker in lowered for marker in reproduce_markers):
        return "reproduce"

    research_markers = (
        "research",
        "idea",
        "design",
        "implement",
        "implementation",
        "feature",
        "subroutine",
        "optimize",
        "optimization",
        "screen",
        "sweep",
        "proposal",
        "benchmark",
        "explore",
    )
    if any(marker in lowered for marker in research_markers):
        return "research"

    loop_markers = (
        "slurm",
        "queue",
        "monitor",
        "overnight",
        "long-running",
        "long running",
        "iterative",
        "wait for",
        "hours",
        "multi-step",
        "multi step",
    )
    if any(marker in lowered for marker in loop_markers):
        return "loop"
    return "exec"


def _select_simulation_workspace() -> tuple[Path | None, bool]:
    cli = _cli()
    repo_dir = Path.cwd()
    runner_app = cli._load_runner_app_module()
    if runner_app._is_valid_git_repo(repo_dir):
        return repo_dir, False

    print("Current directory is not a git repository.")
    print("1. Create ./fermilink-workspace (recommended)")
    print("2. Initialize git here")
    print("3. Cancel")
    answer = _prompt_line("Choose workspace handling [1-3]: ").strip() or "1"
    if answer == "1":
        base = repo_dir / _ZERO_ARG_DEFAULT_WORKSPACE_NAME
        target = base
        counter = 2
        while target.exists() and any(target.iterdir()):
            target = repo_dir / f"{_ZERO_ARG_DEFAULT_WORKSPACE_NAME}-{counter}"
            counter += 1
        target.mkdir(parents=True, exist_ok=True)
        return target, True
    if answer == "2":
        return repo_dir, True
    return None, False


def _confirm_provider_login_if_needed(state: dict[str, object]) -> bool:
    selected_provider = state.get("selected_provider")
    if not isinstance(selected_provider, str):
        print("No provider is configured yet.")
        return False
    provider_scan = state["provider_scan"]
    auth_state = provider_scan.get(selected_provider, {}).get("auth_state")
    if auth_state == "ready":
        return True
    if _prompt_confirm(
        f"Have you already logged into `{selected_provider}` on this machine?",
        default=False,
    ):
        return True
    print(f"Run `{_login_command_for_provider(selected_provider)}` first, then retry.")
    return False


def _confirm_zero_arg_agent_task_ready(
    state: dict[str, object], *, task_label: str
) -> bool:
    selected_provider = state.get("selected_provider")
    if not isinstance(selected_provider, str):
        print(f"A provider must be configured before {task_label}.")
        return False
    return _confirm_provider_login_if_needed(state)


def _run_zero_arg_compile_setup(state: dict[str, object]) -> int | None:
    if not _confirm_zero_arg_agent_task_ready(
        state,
        task_label="compiling a local package",
    ):
        return None

    raw_package_id = _prompt_line("Package id to register: ").strip()
    package_id = _normalize_zero_arg_package_id(raw_package_id)
    if package_id is None:
        return None

    raw_project_path = _prompt_line("Local project path to compile: ").strip()
    if not raw_project_path:
        print("Local project path is required.")
        return None
    project_root = _resolve_existing_zero_arg_dir(
        raw_project_path,
        label="Compile path",
    )
    if project_root is None:
        return None

    install_off = _prompt_confirm(
        "Only refresh local skills without installing into FermiLink storage?",
        default=False,
    )
    docs_only = _prompt_confirm(
        "Use docs-only generation mode for this compile?",
        default=False,
    )
    strict_validation = _prompt_confirm(
        "Fail if compile validation finds broken links or missing playbook requirements?",
        default=False,
    )

    argv = ["compile", package_id, str(project_root)]
    if docs_only:
        argv.append("--docs-only")
    if strict_validation:
        argv.append("--strict-compile-validation")
    if install_off:
        argv.append("--install-off")
    else:
        active_package = str(state["packages"].get("active_package") or "").strip()
        activate_default = not active_package or active_package == package_id
        if _prompt_confirm(
            "Activate this package after compile+install?",
            default=activate_default,
        ):
            argv.append("--activate")

    print(f"About to run: {_render_zero_arg_command(argv)}")
    if not _prompt_confirm("Proceed?", default=True):
        print("Compile launch cancelled.")
        return None
    return _execute_cli_argv(argv)


def _prompt_zero_arg_recompile_mode() -> str:
    print("Update source:")
    print("1. Research pipeline or manuscript")
    print("2. Memory suggestions from workspace runs")
    print("3. Cancel")
    answer = _prompt_line("Choose update source [1-3]: ").strip().lower()
    if not answer:
        return "paper"
    if answer == "1":
        return "paper"
    if answer == "2":
        return "memory"
    if answer == "3":
        return "cancel"
    return ""


def _prompt_zero_arg_memory_scope() -> str | None:
    print("Memory scope:")
    print("1. all")
    print("2. package-specific (machine-independent/shareable)")
    print("3. machine-specific (local-machine guidance)")
    answer = _prompt_line("Choose memory scope [1-3] (Enter for all): ").strip().lower()
    if answer in {"", "1", "all"}:
        return None
    if answer in {"2", "package-specific", "machine-independent"}:
        return "package-specific"
    if answer in {"3", "machine-specific"}:
        return "machine-specific"
    return ""


def _run_zero_arg_recompile_setup(state: dict[str, object]) -> int | None:
    if not _confirm_zero_arg_agent_task_ready(
        state,
        task_label="updating package skills",
    ):
        return None

    raw_package_id = _prompt_line("Package id to update: ").strip()
    package_id = _normalize_zero_arg_package_id(raw_package_id)
    if package_id is None:
        return None

    raw_project_path = _prompt_line(
        "Local package path to recompile (blank to use the installed package): "
    ).strip()
    project_root: Path | None = None
    if raw_project_path:
        project_root = _resolve_existing_zero_arg_dir(
            raw_project_path,
            label="Recompile path",
        )
        if project_root is None:
            return None

    mode = _prompt_zero_arg_recompile_mode()
    if mode == "cancel":
        print("Recompile setup cancelled.")
        return None
    if mode not in {"paper", "memory"}:
        print("Invalid update source selection.")
        return None

    argv = ["recompile", package_id]
    if isinstance(project_root, Path):
        argv.append(str(project_root))

    if mode == "paper":
        raw_doc_path = _prompt_line(
            "Path to the manuscript or workflow description file (--doc): "
        ).strip()
        if not raw_doc_path:
            print("A document path is required for research-pipeline updates.")
            return None
        doc_path = _resolve_existing_zero_arg_file(
            raw_doc_path,
            label="--doc",
        )
        if doc_path is None:
            return None
        argv.extend(["--doc", str(doc_path)])

        raw_data_dir = _prompt_line(
            "Optional supplementary data directory (--data-dir, blank to skip): "
        ).strip()
        if raw_data_dir:
            data_dir = _resolve_existing_zero_arg_dir(
                raw_data_dir,
                label="--data-dir",
            )
            if data_dir is None:
                return None
            argv.extend(["--data-dir", str(data_dir)])

        comment = " ".join(
            _prompt_line(
                "Optional scope comment (--comment, blank to include all key results): "
            ).split()
        ).strip()
        if comment:
            argv.extend(["--comment", comment])

        active_package = str(state["packages"].get("active_package") or "").strip()
        activate_default = not active_package or active_package == package_id
        if _prompt_confirm(
            "Activate this package after recompile?",
            default=activate_default,
        ):
            argv.append("--activate")
    else:
        raw_memory_path = _prompt_line(
            "Path to memory.md or a directory containing memory files (--memory): "
        ).strip()
        if not raw_memory_path:
            print("A memory file or directory is required for memory-driven updates.")
            return None
        memory_path = _resolve_existing_zero_arg_file_or_dir(
            raw_memory_path,
            label="--memory",
        )
        if memory_path is None:
            return None
        argv.extend(["--memory", str(memory_path)])
        scope = _prompt_zero_arg_memory_scope()
        if scope == "":
            print("Invalid memory scope selection.")
            return None
        if isinstance(scope, str):
            argv.extend(["--memory-scope", scope])
        print(
            "Memory-plan mode updates package skills in place and skips package install/activation."
        )

    print(f"About to run: {_render_zero_arg_command(argv)}")
    if not _prompt_confirm("Proceed?", default=True):
        print("Recompile launch cancelled.")
        return None
    return _execute_cli_argv(argv)


def _run_zero_arg_simulation(state: dict[str, object]) -> int | None:
    selected_provider = state.get("selected_provider")
    if not isinstance(selected_provider, str):
        print("A provider must be configured before running simulations.")
        return None
    if not state["packages"]["has_packages"]:
        print(
            "At least one scientific package must be installed before running simulations."
        )
        return None
    if not _confirm_provider_login_if_needed(state):
        return None

    prompt = _prompt_line("Describe the simulation you want to run: ").strip()
    if not prompt:
        print("Simulation prompt is required.")
        return None

    inferred_mode = _infer_zero_arg_mode(prompt)
    override = (
        _prompt_line(
            f"Inferred mode: {inferred_mode}. Press Enter to accept or type exec/loop/research/reproduce: "
        )
        .strip()
        .lower()
    )
    mode = (
        override
        if override in {"exec", "loop", "research", "reproduce"}
        else inferred_mode
    )

    workspace_dir, init_git = _select_simulation_workspace()
    if not isinstance(workspace_dir, Path):
        print("Simulation launch cancelled.")
        return None

    argv = [mode, prompt]
    hpc = state["hpc"]
    if hpc["profile_valid"] and mode in {"exec", "loop", "research", "reproduce"}:
        if _prompt_confirm(
            f"Use default HPC profile {hpc['profile_path']}?",
            default=False,
        ):
            argv.extend(["--hpc-profile", str(hpc["profile_path"])])
    if init_git and mode in {"exec", "loop", "research", "reproduce"}:
        argv.append("--init-git")

    print(f"About to run: fermilink {' '.join(argv[:-1])} <prompt>")
    if not _prompt_confirm("Proceed?", default=True):
        print("Simulation launch cancelled.")
        return None

    with _pushd(workspace_dir):
        return _execute_cli_argv(argv)


def _run_zero_arg_main_menu() -> int:
    while True:
        state = _probe_zero_arg_state()
        print()
        _print_zero_arg_status_summary(state, include_help_hint=False)
        print()
        choice = _prompt_menu_choice()
        if choice == "continue_setup":
            refreshed = _probe_zero_arg_state()
            if refreshed["provider_setup_needed"]:
                _run_zero_arg_provider_setup(refreshed)
                continue
            if not refreshed["packages"]["has_packages"]:
                _run_zero_arg_package_setup(refreshed)
                continue
            if not refreshed["telegram"]["token_present"]:
                result = _run_zero_arg_telegram_setup(refreshed)
                if isinstance(result, int):
                    return result
                continue
            if not refreshed["hpc"]["profile_valid"]:
                _run_zero_arg_hpc_setup(refreshed)
                continue
            print("Core setup is complete.")
            continue
        if choice == "run_simulation":
            result = _run_zero_arg_simulation(state)
            if isinstance(result, int):
                return result
            continue
        if choice == "install_packages":
            _run_zero_arg_package_setup(state)
            continue
        if choice == "open_web_ui":
            services = state["services"]
            if services["runner"].get("running") and services["web"].get("running"):
                print("Web UI is already running at http://127.0.0.1:7860")
                continue
            return _execute_cli_argv(["start"])
        if choice == "setup_telegram":
            result = _run_zero_arg_telegram_setup(state)
            if isinstance(result, int):
                return result
            continue
        if choice == "configure_hpc":
            _run_zero_arg_hpc_setup(state)
            continue
        if choice == "compile_package":
            result = _run_zero_arg_compile_setup(state)
            if isinstance(result, int):
                return result
            continue
        if choice == "recompile_package":
            result = _run_zero_arg_recompile_setup(state)
            if isinstance(result, int):
                return result
            continue
        if choice == "show_status":
            _print_zero_arg_status_summary(state, include_help_hint=False)
            continue
        if choice == "quit":
            return 0
        print("Invalid menu choice.")


def _run_zero_arg_entrypoint() -> int:
    state = _probe_zero_arg_state()
    if not _interactive_tty():
        _print_zero_arg_status_summary(state, include_help_hint=True)
        return 0

    _print_zero_arg_hero_banner()
    if state["provider_setup_needed"]:
        _run_zero_arg_provider_setup(state)
    state = _probe_zero_arg_state()
    if not state["packages"]["has_packages"]:
        _run_zero_arg_package_setup(state)
    return _run_zero_arg_main_menu()
