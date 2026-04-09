from __future__ import annotations

import argparse
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from fermilink.cli import (
    compile_helpers,
    exec_runtime,
    input_repo_helpers,
    overlay_helpers,
    parser_builder,
    routing_helpers,
    runtime_loaders,
    shared_helpers,
)
from fermilink.cli.zero_arg import (
    _effective_argv,
    _execute_cli_argv,
    _run_zero_arg_entrypoint,
)
from fermilink.cli.commands import agent as agent_commands
from fermilink.cli.commands import gateway as gateway_commands
from fermilink.cli.commands import optimize as optimize_commands
from fermilink.cli.commands import packages as package_commands
from fermilink.cli.commands import services as service_commands
from fermilink.cli.commands import sessions as session_commands
from fermilink.cli.commands import workspace as workspace_commands
from fermilink.cli.commands import workflows as workflow_commands
from fermilink.cli.compile_prompts import (
    COMPILE_EVIDENCE_DIR_REL_PATH,
    COMPILE_MEMORY_REL_PATH,
    COMPILE_PROFILE_REL_PATH,
    COMPILE_PROFILE_TAG,
    COMPILE_SKILL_PLAN_REL_PATH,
    COMPILE_SKILL_PLAN_TAG,
    COMPILE_SKILL_PLAN_TOKEN_RE,
    COMPILE_PROFILE_TOKEN_RE,
    COMPILE_REPORT_REL_PATH,
    COMPILE_PROMPT_1,
    COMPILE_PROMPT_2,
    COMPILE_PROMPT_3,
    RECOMPILE_COVERAGE_REL_PATH,
    RECOMPILE_MEMORY_PLAN_REL_PATH,
    RECOMPILE_MEMORY_PLAN_TAG,
    RECOMPILE_MEMORY_PLAN_TOKEN_RE,
    RECOMPILE_MEMORY_PROMPT_1_PLAN,
    RECOMPILE_PAPER_FIGURE_DATA_MAP_REL_PATH,
    RECOMPILE_PAPER_CONTEXT_DIR_REL_PATH,
    RECOMPILE_PAPER_CONTEXT_REL_PATH,
    RECOMPILE_PAPER_PLAN_REL_PATH,
    RECOMPILE_PAPER_PLAN_TAG,
    RECOMPILE_PAPER_PLAN_TOKEN_RE,
    RECOMPILE_PAPER_PROMPT_1,
    RECOMPILE_PAPER_PROMPT_1_PLAN,
    RECOMPILE_PAPER_PROMPT_2,
    RECOMPILE_PAPER_PROMPT_2_TUTORIAL,
    RECOMPILE_PAPER_PROMPT_3,
    RECOMPILE_PAPER_PROMPT_3_AUDIT,
    RECOMPILE_PAPER_SKILL_MANIFEST_REL_PATH,
    RECOMPILE_PAPER_STAGED_ASSETS_DIR_REL_PATH,
    RECOMPILE_PAPER_STAGED_ASSETS_MANIFEST_REL_PATH,
    RECOMPILE_PROMPT_1,
    RECOMPILE_PROMPT_2,
    RECOMPILE_PROMPT_3,
)
from fermilink.cli.parser_agent import register_agent_parser
from fermilink.cli.parser_gateway import register_gateway_parser
from fermilink.cli.parser_optimize import register_optimize_parser
from fermilink.cli.parser_packages import (
    register_package_install_compile_parsers,
    register_package_management_parsers,
)
from fermilink.cli.parser_services import register_service_parsers
from fermilink.cli.parser_sessions import (
    register_chat_parser,
    register_exec_loop_parsers,
)
from fermilink.cli.parser_workspace import register_workspace_parsers
from fermilink.cli.parser_workflows import register_workflow_parsers
from fermilink.cli.workflow_prompts import (
    LOOP_DONE_TOKEN,
    LOOP_MEMORY_DIRNAME,
    LOOP_MEMORY_FILENAME,
    LOOP_PROMPT_PREFIX,
    LOOP_WAIT_TOKEN_RE,
    REPRODUCE_ARCHIVE_DIRNAME,
    REPRODUCE_AUDITOR_PROMPT_PREFIX,
    REPRODUCE_LATEST_RUN_FILENAME,
    REPRODUCE_LOGS_DIRNAME,
    REPRODUCE_PLAN_FILENAME,
    REPRODUCE_PLAN_TAG,
    REPRODUCE_PLAN_TOKEN_RE,
    REPRODUCE_PLANNER_PROMPT_PREFIX,
    REPRODUCE_PROMPTS_DIRNAME,
    REPRODUCE_RUNS_DIR,
    REPRODUCE_STATE_FILENAME,
    RESEARCH_AUDITOR_PROMPT_PREFIX,
    RESEARCH_PLAN_TAG,
    RESEARCH_PLAN_TOKEN_RE,
    RESEARCH_PLANNER_PROMPT_PREFIX,
    RESEARCH_RUNS_DIR,
    WORKFLOW_REPORT_AUDITOR_PROMPT_PREFIX,
    WORKFLOW_REPORT_FILENAME,
    WORKFLOW_REPORT_GENERATOR_PROMPT_PREFIX,
    WORKFLOW_SUMMARIES_DIRNAME,
    UNIFIED_MEMORY_PROMPT_PREFIX,
)
from fermilink.agent_runtime import (
    SUPPORTED_REASONING_EFFORTS,
    SUPPORTED_PROVIDERS,
    load_agent_runtime_policy,
    resolve_agent_runtime_policy,
    save_agent_runtime_policy,
)
from fermilink.config import resolve_runtime_root, resolve_scipkg_root
from fermilink.packages.curated_channels import (
    list_curated_packages,
    normalize_channel_id,
    resolve_curated_package,
    select_package_version,
)
from fermilink.packages.package_registry import (
    PackageError,
    PackageNotFoundError,
    PackageValidationError,
    activate_package,
    delete_package,
    install_from_local_path,
    install_from_zip,
    list_packages,
    load_registry,
    normalize_package_id,
    save_registry,
    set_package_dependency_ids,
    set_package_overlay_entries,
)
from fermilink.providers import (
    build_exec_command,
    collect_provider_service_env_overrides,
    provider_supports_auto_compile_metadata_generation,
    provider_bin_env_key,
    resolve_provider_binary,
    resolve_provider_binary_override,
)
from fermilink.router_rules import sync_router_rules
from fermilink.services import (
    default_service_specs,
    normalize_components,
    service_status,
    start_service,
    stop_service,
)


DEFAULT_MAX_ZIP_BYTES = int(
    os.getenv("FERMILINK_SCIPKG_MAX_ZIP_BYTES", str(800 * 1024 * 1024))
)
DEFAULT_BOOTSTRAP_PACKAGE_ID = "maxwelllink"
DEFAULT_BOOTSTRAP_CHANNEL = "skilled-scipkg"
DEFAULT_PROVIDER_BINARY_OVERRIDE = os.getenv("FERMILINK_CODEX_BIN", "codex")
DEFAULT_COMPILE_SANDBOX = os.getenv("FERMILINK_COMPILE_SANDBOX", "workspace-write")

EXEC_ROUTER_ENABLED = os.getenv(
    "FERMILINK_CHAINLIT_PACKAGE_ROUTER_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
EXEC_ROUTER_AUTO_DEFAULT = os.getenv(
    "FERMILINK_CHAINLIT_PACKAGE_ROUTER_AUTO", "true"
).strip().lower() in {"1", "true", "yes", "on"}
EXEC_SECOND_GUESS_ENABLED = os.getenv(
    "FERMILINK_PACKAGE_SECOND_GUESS_ENABLED", "true"
).strip().lower() in {"1", "true", "yes", "on"}
try:
    EXEC_SECOND_GUESS_MIN_CONFIDENCE = float(
        os.getenv("FERMILINK_CHAINLIT_PACKAGE_SECOND_GUESS_MIN_CONFIDENCE", "0.75")
    )
except ValueError:
    EXEC_SECOND_GUESS_MIN_CONFIDENCE = 0.75
try:
    EXEC_SECOND_GUESS_TIMEOUT_SECONDS = float(
        os.getenv("FERMILINK_CHAINLIT_PACKAGE_SECOND_GUESS_TIMEOUT_SECONDS", "25.0")
    )
except ValueError:
    EXEC_SECOND_GUESS_TIMEOUT_SECONDS = 25.0

PACKAGE_SOURCE_MANUAL = "manual"
PACKAGE_SOURCE_AUTO = "auto"
PACKAGE_SOURCE_DEFAULT = "default"
PACKAGE_SOURCE_SECOND_GUESS = "second_guess"
PACKAGE_SOURCE_NONE = "none"
WEB_ROUTER_ONLY_IMPORT_ENV = "FERMILINK_ROUTER_ONLY_IMPORT"


# Shared formatting/output helpers
_should_style_cli_output = shared_helpers._should_style_cli_output
_style_text = shared_helpers._style_text
_format_cli_tag = shared_helpers._format_cli_tag
_format_tagged_line = shared_helpers._format_tagged_line
_print_tagged = shared_helpers._print_tagged
_chat_input_prompt = shared_helpers._chat_input_prompt
_chat_prompt_spacing_after_input = shared_helpers._chat_prompt_spacing_after_input
_extract_flag_value = shared_helpers._extract_flag_value
_extract_port_from_command = shared_helpers._extract_port_from_command
_service_start_line = shared_helpers._service_start_line
_service_stop_line = shared_helpers._service_stop_line
_service_status_line = shared_helpers._service_status_line
_bootstrap_line = shared_helpers._bootstrap_line


def _print_json(payload: dict) -> None:
    shared_helpers._print_json(payload)


def _print_lines(lines: list[str]) -> None:
    shared_helpers._print_lines(lines)


def _emit_output(args: argparse.Namespace, payload: dict, lines: list[str]) -> None:
    """Render command responses for package/service/agent command families."""

    if getattr(args, "json", False):
        _print_json(payload)
        return
    _print_lines(lines)


# Runtime/module loaders
_load_web_router_module = runtime_loaders._load_web_router_module
_load_runner_app_module = runtime_loaders._load_runner_app_module

# Routing and second-guess helpers
_normalize_installed_package_ids = routing_helpers._normalize_installed_package_ids
_collect_second_guess_assistant_text = (
    routing_helpers._collect_second_guess_assistant_text
)
_run_exec_second_guess = routing_helpers._run_exec_second_guess
_resolve_exec_package_selection = routing_helpers._resolve_exec_package_selection

# Overlay lifecycle helpers
_is_public_overlay_name = overlay_helpers._is_public_overlay_name
_filter_exec_overlay_package_meta = overlay_helpers._filter_exec_overlay_package_meta
_overlay_exec_package = overlay_helpers._overlay_exec_package
_cleanup_exec_overlay_symlinks = overlay_helpers._cleanup_exec_overlay_symlinks

# Exec runtime helpers
_inject_exec_option_before_prompt = exec_runtime._inject_exec_option_before_prompt
_render_claude_stream_event = exec_runtime._render_claude_stream_event
_stream_claude_exec_output = exec_runtime._stream_claude_exec_output
_stream_claude_exec_output_with_capture = (
    exec_runtime._stream_claude_exec_output_with_capture
)
_prepare_provider_runtime_env = exec_runtime._prepare_provider_runtime_env
_cleanup_temp_paths = exec_runtime._cleanup_temp_paths
_stream_exec_process_output = exec_runtime._stream_exec_process_output
_stream_exec_process_output_with_capture = (
    exec_runtime._stream_exec_process_output_with_capture
)
_swap_stop_requested_checker = exec_runtime._swap_stop_requested_checker
_has_stop_requested_checker = exec_runtime._has_stop_requested_checker
_is_stop_requested = exec_runtime._is_stop_requested
_should_use_direct_terminal_stream = exec_runtime._should_use_direct_terminal_stream
_run_exec_chat_turn = exec_runtime._run_exec_chat_turn
_run_exec_provider_prompt = exec_runtime._run_exec_provider_prompt

# Repo and prompt input helpers
_ensure_exec_repo_ready = input_repo_helpers._ensure_exec_repo_ready
_ensure_compile_repo_ready = input_repo_helpers._ensure_compile_repo_ready
_resolve_project_path = input_repo_helpers._resolve_project_path
_resolve_exec_like_user_prompt = input_repo_helpers._resolve_exec_like_user_prompt

# Compile helpers
_resolve_compile_tool_source = compile_helpers._resolve_compile_tool_source
_load_compile_profile = compile_helpers._load_compile_profile
_extract_compile_skill_plan_from_assistant_text = (
    compile_helpers._extract_compile_skill_plan_from_assistant_text
)
_normalize_compile_skill_plan = compile_helpers._normalize_compile_skill_plan
_write_compile_skill_plan = compile_helpers._write_compile_skill_plan
_load_compile_skill_plan = compile_helpers._load_compile_skill_plan
_list_skill_ids = compile_helpers._list_skill_ids
_ensure_compile_memory = compile_helpers._ensure_compile_memory
_reset_compile_memory_short_term = compile_helpers._reset_compile_memory_short_term
_record_compile_memory_run = compile_helpers._record_compile_memory_run
_normalize_recompile_memory_scope = compile_helpers._normalize_recompile_memory_scope
_render_recompile_memory_scope = compile_helpers._render_recompile_memory_scope
_collect_recompile_memory_suggestions = (
    compile_helpers._collect_recompile_memory_suggestions
)
_run_compile_generator = compile_helpers._run_compile_generator
_build_compile_evidence_bundle = compile_helpers._build_compile_evidence_bundle
_build_recompile_evidence_bundle = compile_helpers._build_recompile_evidence_bundle
_snapshot_skills_tree = compile_helpers._snapshot_skills_tree
_diff_skills_tree_snapshot = compile_helpers._diff_skills_tree_snapshot
_assert_skills_change_scope = compile_helpers._assert_skills_change_scope
_extract_recompile_memory_plan_from_assistant_text = (
    compile_helpers._extract_recompile_memory_plan_from_assistant_text
)
_normalize_recompile_memory_plan = compile_helpers._normalize_recompile_memory_plan
_write_recompile_memory_plan = compile_helpers._write_recompile_memory_plan
_apply_recompile_memory_plan = compile_helpers._apply_recompile_memory_plan
_extract_recompile_paper_plan_from_assistant_text = (
    compile_helpers._extract_recompile_paper_plan_from_assistant_text
)
_normalize_recompile_paper_plan = compile_helpers._normalize_recompile_paper_plan
_write_recompile_paper_plan = compile_helpers._write_recompile_paper_plan
_derive_recompile_paper_skill_id = compile_helpers._derive_recompile_paper_skill_id
_ensure_recompile_paper_skill_scaffold = (
    compile_helpers._ensure_recompile_paper_skill_scaffold
)
_initialize_recompile_paper_sidecar_files = (
    compile_helpers._initialize_recompile_paper_sidecar_files
)
_snapshot_recompile_paper_skills = compile_helpers._snapshot_recompile_paper_skills
_diff_recompile_paper_skills_snapshot = (
    compile_helpers._diff_recompile_paper_skills_snapshot
)
_assert_recompile_paper_change_scope = (
    compile_helpers._assert_recompile_paper_change_scope
)
_build_recompile_paper_context = compile_helpers._build_recompile_paper_context
_stage_recompile_paper_assets = compile_helpers._stage_recompile_paper_assets
_validate_recompile_paper_outputs = compile_helpers._validate_recompile_paper_outputs
_validate_compiled_skills = compile_helpers._validate_compiled_skills
_load_previous_source_inventory = compile_helpers._load_previous_source_inventory
_write_compile_report = compile_helpers._write_compile_report
_run_compile_provider_pass = compile_helpers._run_compile_provider_pass

# Workflow internals moved to fermilink.cli.commands.workflows.
_utc_now_z = workflow_commands._utc_now_z
_write_json_atomic = workflow_commands._write_json_atomic
_normalize_string_list = workflow_commands._normalize_string_list
_sanitize_task_id = workflow_commands._sanitize_task_id
_render_reproduce_task_prompt = workflow_commands._render_reproduce_task_prompt
_extract_tagged_json_payload = workflow_commands._extract_tagged_json_payload
_extract_reproduce_plan_payload = workflow_commands._extract_reproduce_plan_payload
_extract_research_plan_payload = workflow_commands._extract_research_plan_payload
_normalize_automation_plan = workflow_commands._normalize_automation_plan
_normalize_reproduce_plan = workflow_commands._normalize_reproduce_plan
_normalize_research_plan = workflow_commands._normalize_research_plan
_run_reproduce_exec_turn = workflow_commands._run_reproduce_exec_turn
_generate_mode_plan = workflow_commands._generate_mode_plan
_generate_reproduce_plan = workflow_commands._generate_reproduce_plan
_generate_research_plan = workflow_commands._generate_research_plan
_resolve_invocation_hpc_context = workflow_commands._resolve_invocation_hpc_context
_build_hpc_prompt_lines = workflow_commands._build_hpc_prompt_lines
_resolve_invocation_data_context = workflow_commands._resolve_invocation_data_context
_prepare_workflow_data_artifacts = workflow_commands._prepare_workflow_data_artifacts
_archive_loop_memory = workflow_commands._archive_loop_memory
_ensure_loop_memory = workflow_commands._ensure_loop_memory
_reset_loop_short_term_memory = workflow_commands._reset_loop_short_term_memory
_extract_loop_wait_seconds = workflow_commands._extract_loop_wait_seconds
_extract_loop_pid_numbers = workflow_commands._extract_loop_pid_numbers
_extract_loop_slurm_job_numbers = workflow_commands._extract_loop_slurm_job_numbers
_materialize_mode_plan = workflow_commands._materialize_mode_plan
_maybe_sync_mode_plan_from_disk = workflow_commands._maybe_sync_mode_plan_from_disk
_finalize_workflow_report = workflow_commands._finalize_workflow_report
_workflow_completion_commit = workflow_commands._workflow_completion_commit

# Command entrypoint aliases
_cmd_chat = session_commands.cmd_chat
_cmd_loop = session_commands.cmd_loop
_cmd_exec = session_commands.cmd_exec
_cmd_init = workspace_commands.cmd_init
_cmd_clean = workspace_commands.cmd_clean
_cmd_hpc = workspace_commands.cmd_hpc
_cmd_gateway = gateway_commands.cmd_gateway
_cmd_optimize = optimize_commands.cmd_optimize

_cmd_plan_workflow = workflow_commands.cmd_plan_workflow
_cmd_reproduce = workflow_commands.cmd_reproduce
_cmd_research = workflow_commands.cmd_research

_cmd_compile = package_commands.cmd_compile
_cmd_recompile = package_commands.cmd_recompile
_cmd_auto_compile = package_commands.cmd_auto_compile
_save_curated_install_metadata = package_commands._save_curated_install_metadata
_cmd_install = package_commands.cmd_install
_cmd_list = package_commands.cmd_list
_cmd_avail = package_commands.cmd_avail
_cmd_activate = package_commands.cmd_activate
_collect_csv_and_repeat = package_commands._collect_csv_and_repeat
_cmd_overlay = package_commands.cmd_overlay
_cmd_dependencies = package_commands.cmd_dependencies
_cmd_delete = package_commands.cmd_delete

_resolve_specs = service_commands._resolve_specs
_installed_package_count = service_commands._installed_package_count
_ensure_bootstrap_package_for_services = (
    service_commands._ensure_bootstrap_package_for_services
)
_is_start_result_failed = service_commands._is_start_result_failed
_start_sequence = service_commands._start_sequence
_cmd_start = service_commands.cmd_start
_cmd_stop = service_commands.cmd_stop
_cmd_restart = service_commands.cmd_restart
_cmd_status = service_commands.cmd_status

_cmd_agent = agent_commands.cmd_agent

# Parser builder
_build_parser = parser_builder._build_parser


def main(argv: list[str] | None = None) -> int:
    """
    Run the FermiLink CLI entrypoint.

    Parameters
    ----------
    argv : list[str] | None
        Optional CLI argv sequence; defaults to process argv when omitted.

    Returns
    -------
    int
        Process exit code (`0` on success).
    """
    effective_argv = _effective_argv(argv)
    if not effective_argv:
        return _run_zero_arg_entrypoint()
    return _execute_cli_argv(effective_argv)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
