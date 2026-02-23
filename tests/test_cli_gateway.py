from __future__ import annotations
import tempfile
import time
from pathlib import Path

from fermilink import cli
from fermilink.cli.commands import gateway as gateway_commands


def _loop_config() -> gateway_commands.GatewayLoopConfig:
    return gateway_commands.GatewayLoopConfig(
        package_id=None,
        sandbox=None,
        codex_bin="codex",
        max_iterations=2,
        wait_seconds=0.0,
        max_wait_seconds=10.0,
        pid_stall_seconds=0.0,
        init_git=True,
    )


def test_gateway_parser_supports_loop_forwarding_flags() -> None:
    parser = cli._build_parser()
    args = parser.parse_args(
        [
            "gateway",
            "--telegram-token",
            "token-123",
            "--allow-from",
            "12345,alice",
            "--allow-from",
            "67890",
            "--package",
            "maxwelllink",
            "--sandbox",
            "read-only",
            "--max-iterations",
            "7",
            "--wait-seconds",
            "2",
            "--max-wait-seconds",
            "60",
            "--pid-stall-seconds",
            "11",
            "--hpc-profile",
            "scripts/hpc_profile_anvil.json",
            "--no-init-git",
        ]
    )
    assert args.telegram_token == "token-123"
    assert args.allow_from == ["12345,alice", "67890"]
    assert args.package_id == "maxwelllink"
    assert args.sandbox == "read-only"
    assert args.max_iterations == 7
    assert args.wait_seconds == 2.0
    assert args.max_wait_seconds == 60.0
    assert args.pid_stall_seconds == 11.0
    assert args.hpc_profile == "scripts/hpc_profile_anvil.json"
    assert args.init_git is False


def test_gateway_state_round_trip_preserves_active_workspace(tmp_path: Path) -> None:
    state_path = tmp_path / "chat_sessions.json"
    state = gateway_commands._default_gateway_state()
    telegram = gateway_commands._telegram_state(state)
    chat_state = gateway_commands._ensure_chat_state(telegram, "telegram:42")
    workspace = gateway_commands._create_workspace(
        chat_state,
        chat_id="42",
        requested_label="main",
        created_via="new",
    )
    chat_state["execution_mode"] = "exec"
    chat_state["last_run_status"] = "done"
    chat_state["last_run_reason"] = "exec_completed"
    chat_state["last_run_exit_code"] = 0
    chat_state["last_run_mode"] = "exec"

    gateway_commands._save_gateway_state(state_path, state)
    loaded = gateway_commands._load_gateway_state(state_path)
    loaded_chat = gateway_commands._ensure_chat_state(
        gateway_commands._telegram_state(loaded), "telegram:42"
    )

    assert loaded_chat["active_workspace_id"] == workspace["id"]
    assert len(loaded_chat["workspaces"]) == 1
    assert loaded_chat["workspaces"][0]["label"] == "main"
    assert loaded_chat["execution_mode"] == "exec"
    assert loaded_chat["last_run_status"] == "done"
    assert loaded_chat["last_run_reason"] == "exec_completed"
    assert loaded_chat["last_run_exit_code"] == 0
    assert loaded_chat["last_run_mode"] == "exec"


def test_gateway_state_round_trip_preserves_workflow_mode(tmp_path: Path) -> None:
    state_path = tmp_path / "chat_sessions.json"
    state = gateway_commands._default_gateway_state()
    telegram = gateway_commands._telegram_state(state)
    chat_state = gateway_commands._ensure_chat_state(telegram, "telegram:43")
    gateway_commands._create_workspace(
        chat_state,
        chat_id="43",
        requested_label="main",
        created_via="new",
    )
    chat_state["execution_mode"] = "research"

    gateway_commands._save_gateway_state(state_path, state)
    loaded = gateway_commands._load_gateway_state(state_path)
    loaded_chat = gateway_commands._ensure_chat_state(
        gateway_commands._telegram_state(loaded), "telegram:43"
    )

    assert loaded_chat["execution_mode"] == "research"


def test_run_loop_in_workspace_forwards_iteration_hook(
    monkeypatch, tmp_path: Path
) -> None:
    captured_iterations: list[tuple[int, int]] = []
    captured_hpc_profile: str | None = None

    class _FakeCli:
        def _cmd_loop(self, args: object) -> int:
            nonlocal captured_hpc_profile
            captured_hpc_profile = getattr(args, "hpc_profile", None)
            hook = getattr(args, "_fermilink_loop_iteration_hook", None)
            if callable(hook):
                hook(2, 10)
            setattr(
                args,
                "_fermilink_loop_outcome",
                {"status": "done", "reason": "done_token"},
            )
            return 0

    monkeypatch.setattr(gateway_commands, "_cli", lambda: _FakeCli())
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    loop_config = gateway_commands.GatewayLoopConfig(
        package_id=None,
        sandbox=None,
        codex_bin="codex",
        max_iterations=10,
        wait_seconds=0.0,
        max_wait_seconds=60.0,
        pid_stall_seconds=5.0,
        init_git=True,
        hpc_profile="scripts/hpc_profile_anvil.json",
        loop_iteration_hook=lambda iteration, maximum: captured_iterations.append(
            (iteration, maximum)
        ),
    )
    code, outcome = gateway_commands._run_loop_in_workspace(
        repo_dir,
        "run a loop task",
        loop_config,
    )

    assert code == 0
    assert isinstance(outcome, dict)
    assert outcome.get("status") == "done"
    assert captured_iterations == [(2, 10)]
    assert captured_hpc_profile == "scripts/hpc_profile_anvil.json"


def test_run_loop_in_workspace_captures_last_informative_reply(
    monkeypatch, tmp_path: Path
) -> None:
    class _FakeCli:
        LOOP_DONE_TOKEN = "<promise>DONE</promise>"

        def __init__(self) -> None:
            self._turn = 0

        def _run_exec_chat_turn(
            self, *args: object, **kwargs: object
        ) -> dict[str, object]:
            self._turn += 1
            if self._turn == 1:
                return {
                    "assistant_text": (
                        "Prepared run artifacts.\n" "<pid_number>12345</pid_number>"
                    ),
                    "return_code": 0,
                    "stderr": "",
                }
            return {
                "assistant_text": "<promise>DONE</promise>",
                "return_code": 0,
                "stderr": "",
            }

        def _cmd_loop(self, args: object) -> int:
            self._run_exec_chat_turn()
            self._run_exec_chat_turn()
            setattr(
                args,
                "_fermilink_loop_outcome",
                {"status": "done", "reason": "done_token"},
            )
            return 0

    monkeypatch.setattr(gateway_commands, "_cli", lambda: _FakeCli())
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    code, outcome = gateway_commands._run_loop_in_workspace(
        repo_dir,
        "run and summarize",
        _loop_config(),
    )

    assert code == 0
    assert isinstance(outcome, dict)
    assert outcome.get("agent_reply_source") == "loop_last_informative_turn"
    assert outcome.get("agent_reply_text") == "Prepared run artifacts."
    assert outcome.get("loop_done_token_seen") is True
    assert outcome.get("loop_turn_count") == 2


def test_run_exec_in_workspace_captures_last_message(
    monkeypatch, tmp_path: Path
) -> None:
    captured_hpc_profile: str | None = None

    class _FakeCli:
        tempfile = tempfile

        def _inject_exec_option_before_prompt(
            self, command: list[str], *option_tokens: str
        ) -> list[str]:
            if not command:
                return command
            prompt_arg = command[-1]
            return [*command[:-1], *option_tokens, prompt_arg]

        def build_exec_command(
            self,
            *,
            provider: str,
            provider_bin: str,
            repo_dir: Path,
            prompt: str,
            sandbox_policy: str = "enforce",
            sandbox_mode: str | None = None,
            json_output: bool = True,
        ) -> list[str]:
            del (
                provider,
                provider_bin,
                repo_dir,
                sandbox_policy,
                sandbox_mode,
                json_output,
            )
            return ["codex", "exec", prompt]

        def _cmd_exec(self, args: object) -> int:
            nonlocal captured_hpc_profile
            captured_hpc_profile = getattr(args, "hpc_profile", None)
            prompt = str(getattr(args, "prompt")[0])
            command = self.build_exec_command(
                provider="codex",
                provider_bin="codex",
                repo_dir=Path.cwd(),
                prompt=prompt,
                sandbox_policy="enforce",
                sandbox_mode=None,
                json_output=False,
            )
            output_index = command.index("--output-last-message")
            output_path = Path(command[output_index + 1])
            output_path.write_text(
                "Exact exec reply with final recommendation.",
                encoding="utf-8",
            )
            return 0

    monkeypatch.setattr(gateway_commands, "_cli", lambda: _FakeCli())
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    code, outcome = gateway_commands._run_exec_in_workspace(
        repo_dir,
        "single turn request",
        gateway_commands.GatewayLoopConfig(
            package_id=None,
            sandbox=None,
            codex_bin="codex",
            max_iterations=2,
            wait_seconds=0.0,
            max_wait_seconds=10.0,
            pid_stall_seconds=0.0,
            init_git=True,
            hpc_profile="scripts/hpc_profile_anvil.json",
        ),
    )

    assert code == 0
    assert isinstance(outcome, dict)
    assert outcome.get("status") == "done"
    assert outcome.get("agent_reply_source") == "exec_last_message"
    assert (
        outcome.get("agent_reply_text") == "Exact exec reply with final recommendation."
    )
    assert captured_hpc_profile == "scripts/hpc_profile_anvil.json"


def test_run_research_in_workspace_forwards_hpc_profile(
    monkeypatch, tmp_path: Path
) -> None:
    status_updates: list[str] = []

    class _FakeCli:
        def __init__(self) -> None:
            self.research_args = None

        def _cmd_research(self, args: object) -> int:
            self.research_args = args
            return 0

        def _cmd_reproduce(self, _args: object) -> int:
            raise AssertionError("reproduce runner should not be called")

    fake_cli = _FakeCli()
    monkeypatch.setattr(gateway_commands, "_cli", lambda: fake_cli)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    loop_config = gateway_commands.GatewayLoopConfig(
        package_id="maxwelllink",
        sandbox="workspace-write",
        codex_bin="codex",
        max_iterations=8,
        wait_seconds=2.0,
        max_wait_seconds=90.0,
        pid_stall_seconds=12.0,
        init_git=True,
        hpc_profile="scripts/hpc_profile_anvil.json",
        workflow_status_hook=lambda mode_text: status_updates.append(mode_text),
    )
    code, outcome = gateway_commands._run_research_in_workspace(
        repo_dir,
        "research prompt",
        loop_config,
    )

    assert code == 0
    assert isinstance(outcome, dict)
    assert outcome.get("status") == "done"
    assert outcome.get("reason") == "research_completed"
    assert fake_cli.research_args is not None
    assert fake_cli.research_args.command == "research"
    assert fake_cli.research_args.prompt == ["research prompt"]
    assert fake_cli.research_args.max_iterations == 8
    assert fake_cli.research_args.wait_seconds == 2.0
    assert fake_cli.research_args.max_wait_seconds == 90.0
    assert fake_cli.research_args.pid_stall_seconds == 12.0
    assert fake_cli.research_args.hpc_profile == "scripts/hpc_profile_anvil.json"
    assert fake_cli.research_args.plan_only is False
    assert fake_cli.research_args.report_only is False
    assert callable(fake_cli.research_args._fermilink_workflow_status_hook)
    fake_cli.research_args._fermilink_workflow_status_hook("research task 1/3 loop 2/8")
    assert status_updates == ["research task 1/3 loop 2/8"]


def test_strip_loop_control_lines_removes_machine_tags() -> None:
    raw = (
        "Progress update: generated output files.\n"
        "<pid_number>12345</pid_number>\n"
        "<slurm_job_number>12345_7.batch</slurm_job_number>\n"
        "<wait_seconds>15</wait_seconds>\n"
        "<promise>DONE</promise>\n"
    )
    cleaned = gateway_commands._strip_loop_control_lines(
        raw,
        done_token="<promise>DONE</promise>",
    )
    assert cleaned == "Progress update: generated output files."


def test_build_agent_reply_section_renders_markdown_to_html() -> None:
    section = gateway_commands._build_agent_reply_section(
        (
            "# Final Summary\n"
            "- **Energy**: `-1.234`\n"
            "- [Report](https://example.com/report)\n"
            "> keep this note\n"
            "```python\n"
            "print('ok')\n"
            "```\n"
        )
    )

    assert "<b>Agent Reply</b>" in section
    assert "<b>Final Summary</b>" in section
    assert "• <b>Energy</b>: <code>-1.234</code>" in section
    assert '<a href="https://example.com/report">Report</a>' in section
    assert "<i>&gt; keep this note</i>" in section
    assert "<pre><code class=\"language-python\">print('ok')</code></pre>" in section


def test_handle_telegram_text_supports_sticky_new_and_use(tmp_path: Path) -> None:
    state = gateway_commands._default_gateway_state()
    workspaces_root = tmp_path / "workspaces"
    run_paths: list[Path] = []

    def fake_repo_ensurer(repo_dir: Path, _init_git: bool) -> None:
        projects_dir = repo_dir / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        (projects_dir / "scf_result.json").write_text("{}", encoding="utf-8")
        (repo_dir / "projects" / "memory.md").write_text(
            (
                "# FermiLink Unified Memory\n\n"
                "### Plan\n"
                "- [x] Build PySCF input for H2O\n"
                "- [x] Run SCF with HF/3-21g\n"
                "- [ ] Post-check convergence trend\n\n"
                "### Key results\n"
                "- h2o_final_energy | RHF total energy (Hartree) | -75.5854 | "
                "H2O basis 3-21g | projects/scf_result.json\n"
            ),
            encoding="utf-8",
        )

    def fake_loop_runner(
        repo_dir: Path,
        _prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        run_paths.append(repo_dir)
        return 0, {"status": "done", "reason": "done_token"}

    chat_id = "42"
    chat_key = "telegram:42"
    loop_config = _loop_config()
    gateway_commands._handle_telegram_text(
        text="/mode loop",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    first = gateway_commands._handle_telegram_text(
        text="simulate baseline",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    second = gateway_commands._handle_telegram_text(
        text="continue baseline",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    new_reply = gateway_commands._handle_telegram_text(
        text="/new branch",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    third = gateway_commands._handle_telegram_text(
        text="run branch workflow",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    use_reply = gateway_commands._handle_telegram_text(
        text="/use main",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    fourth = gateway_commands._handle_telegram_text(
        text="continue baseline again",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    assert "Run complete in workspace" in first
    assert "The requested simulation workflow finished successfully." in first
    assert "<b>What Was Done</b>" in first
    assert "<b>Key Findings</b>" in first
    assert "<b>Parameter Source Mapping</b>" in first
    assert "<b>Simulation Uncertainty</b>" in first
    assert "<b>Recent Artifacts</b>" in first
    assert "Run complete in workspace" in second
    assert "Switched to new workspace" in new_reply
    assert "Run complete in workspace" in third
    assert "Switched workspace" in use_reply
    assert "Run complete in workspace" in fourth

    assert len(run_paths) == 4
    assert run_paths[0] == run_paths[1]
    assert run_paths[2] != run_paths[0]
    assert run_paths[3] == run_paths[0]


def test_handle_telegram_text_mode_switches_between_loop_and_exec(
    tmp_path: Path,
) -> None:
    state = gateway_commands._default_gateway_state()
    workspaces_root = tmp_path / "workspaces"
    loop_calls: list[Path] = []
    exec_calls: list[Path] = []

    def fake_repo_ensurer(repo_dir: Path, _init_git: bool) -> None:
        (repo_dir / "projects").mkdir(parents=True, exist_ok=True)
        (repo_dir / "projects" / "memory.md").write_text(
            (
                "# FermiLink Unified Memory\n\n"
                "### Plan\n"
                "- [x] Execute request\n\n"
                "### Key results\n"
                "- energy | value | -1.0 | test | projects/result.json\n"
            ),
            encoding="utf-8",
        )

    def fake_loop_runner(
        repo_dir: Path,
        _prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        loop_calls.append(repo_dir)
        return 0, {"status": "done", "reason": "done_token"}

    def fake_exec_runner(
        repo_dir: Path,
        _prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        exec_calls.append(repo_dir)
        return 0, {"status": "done", "reason": "exec_completed"}

    chat_id = "501"
    chat_key = "telegram:501"
    loop_config = _loop_config()

    mode_before = gateway_commands._handle_telegram_text(
        text="/mode",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    set_exec = gateway_commands._handle_telegram_text(
        text="/mode exec",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    exec_run = gateway_commands._handle_telegram_text(
        text="run once",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    set_loop = gateway_commands._handle_telegram_text(
        text="/mode loop",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    loop_run = gateway_commands._handle_telegram_text(
        text="run iterative",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    where = gateway_commands._handle_telegram_text(
        text="/where",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    assert "Current mode: exec" in mode_before
    assert "Execution mode set to exec." in set_exec
    assert "Execution mode: <code>exec</code>." in exec_run
    assert "Single-turn execution finished successfully." in exec_run
    assert "Execution mode set to loop." in set_loop
    assert "Execution mode: <code>loop</code>." in loop_run
    assert "Current mode: loop" in where
    assert len(exec_calls) == 1
    assert len(loop_calls) == 1
    assert exec_calls[0] == loop_calls[0]


def test_handle_telegram_text_mode_switches_to_workflow_modes(
    tmp_path: Path,
) -> None:
    state = gateway_commands._default_gateway_state()
    workspaces_root = tmp_path / "workspaces"
    research_calls: list[tuple[Path, str]] = []
    reproduce_calls: list[tuple[Path, str]] = []

    def fake_repo_ensurer(repo_dir: Path, _init_git: bool) -> None:
        (repo_dir / "projects").mkdir(parents=True, exist_ok=True)
        (repo_dir / "projects" / "memory.md").write_text(
            (
                "# FermiLink Unified Memory\n\n"
                "### Plan\n"
                "- [x] Execute request\n\n"
                "### Key results\n"
                "- energy | value | -1.0 | test | projects/result.json\n"
            ),
            encoding="utf-8",
        )

    def fake_research_runner(
        repo_dir: Path,
        prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        research_calls.append((repo_dir, prompt))
        return 0, {"status": "done", "reason": "research_completed"}

    def fake_reproduce_runner(
        repo_dir: Path,
        prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        reproduce_calls.append((repo_dir, prompt))
        return 0, {"status": "done", "reason": "reproduce_completed"}

    chat_id = "511"
    chat_key = "telegram:511"
    loop_config = _loop_config()

    set_research = gateway_commands._handle_telegram_text(
        text="/mode research",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        research_runner=fake_research_runner,
        reproduce_runner=fake_reproduce_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    research_run = gateway_commands._handle_telegram_text(
        text="draft a cavity qed study plan",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        research_runner=fake_research_runner,
        reproduce_runner=fake_reproduce_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    set_reproduce = gateway_commands._handle_telegram_text(
        text="/mode reproduce",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        research_runner=fake_research_runner,
        reproduce_runner=fake_reproduce_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    reproduce_run = gateway_commands._handle_telegram_text(
        text="paper.md",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        research_runner=fake_research_runner,
        reproduce_runner=fake_reproduce_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    where = gateway_commands._handle_telegram_text(
        text="/where",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        research_runner=fake_research_runner,
        reproduce_runner=fake_reproduce_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    assert "Execution mode set to research." in set_research
    assert "Execution mode: <code>research</code>." in research_run
    assert "Research workflow orchestration finished successfully." in research_run
    assert "Execution mode set to reproduce." in set_reproduce
    assert "Execution mode: <code>reproduce</code>." in reproduce_run
    assert (
        "Reproduce workflow orchestration finished successfully." in reproduce_run
    )
    assert "Current mode: reproduce" in where
    assert len(research_calls) == 1
    assert len(reproduce_calls) == 1
    assert research_calls[0][1] == "draft a cavity qed study plan"
    assert reproduce_calls[0][1] == "paper.md"
    assert research_calls[0][0] == reproduce_calls[0][0]


def test_handle_telegram_text_loopcfg_overrides_apply_without_restart(
    tmp_path: Path,
) -> None:
    state = gateway_commands._default_gateway_state()
    workspaces_root = tmp_path / "workspaces"
    applied_loop_controls: list[tuple[int, float]] = []

    def fake_repo_ensurer(repo_dir: Path, _init_git: bool) -> None:
        (repo_dir / "projects").mkdir(parents=True, exist_ok=True)
        (repo_dir / "projects" / "memory.md").write_text(
            (
                "# FermiLink Unified Memory\n\n"
                "### Plan\n"
                "- [x] Execute request\n\n"
                "### Key results\n"
                "- energy | value | -1.0 | test | projects/result.json\n"
            ),
            encoding="utf-8",
        )

    def fake_loop_runner(
        _repo_dir: Path,
        _prompt: str,
        loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        applied_loop_controls.append(
            (loop_config.max_iterations, loop_config.max_wait_seconds)
        )
        return 0, {"status": "done", "reason": "done_token"}

    chat_id = "502"
    chat_key = "telegram:502"
    base_loop_config = gateway_commands.GatewayLoopConfig(
        package_id=None,
        sandbox=None,
        codex_bin="codex",
        max_iterations=4,
        wait_seconds=0.0,
        max_wait_seconds=20.0,
        pid_stall_seconds=0.0,
        init_git=True,
    )

    before = gateway_commands._handle_telegram_text(
        text="/loopcfg",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=base_loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    updated = gateway_commands._handle_telegram_text(
        text="/loopcfg --max-iterations 7 --max-wait-seconds 45",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=base_loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    invalid = gateway_commands._handle_telegram_text(
        text="/loopcfg --max-iterations 0",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=base_loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    gateway_commands._handle_telegram_text(
        text="/mode loop",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=base_loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    gateway_commands._handle_telegram_text(
        text="run iterative",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=base_loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    reset = gateway_commands._handle_telegram_text(
        text="/loopcfg --reset",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=base_loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    gateway_commands._handle_telegram_text(
        text="run iterative again",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=base_loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    assert "max-iterations: 4 (gateway default)" in before
    assert "max-wait-seconds: 20 (gateway default)" in before
    assert "Loop controls updated for this chat." in updated
    assert "max-iterations: 7 (chat override)" in updated
    assert "max-wait-seconds: 45 (chat override)" in updated
    assert "--max-iterations must be an integer >= 1." in invalid
    assert "Loop controls reset to gateway defaults." in reset
    assert applied_loop_controls == [(7, 45.0), (4, 20.0)]


def test_queue_telegram_run_snapshots_loop_controls_per_job() -> None:
    state = gateway_commands._default_gateway_state()
    telegram = gateway_commands._telegram_state(state)
    chat_key = "telegram:906"
    chat_state = gateway_commands._ensure_chat_state(telegram, chat_key)
    chat_state["loop_max_iterations_override"] = 5
    chat_state["loop_max_wait_seconds_override"] = 30.0
    base_loop_config = gateway_commands.GatewayLoopConfig(
        package_id=None,
        sandbox=None,
        codex_bin="codex",
        max_iterations=2,
        wait_seconds=0.0,
        max_wait_seconds=10.0,
        pid_stall_seconds=0.0,
        init_git=True,
    )

    first, _ = gateway_commands._queue_telegram_run(
        text="first loop request",
        chat_id="906",
        chat_key=chat_key,
        state=state,
        loop_config=base_loop_config,
    )
    chat_state = gateway_commands._ensure_chat_state(telegram, chat_key)
    chat_state["loop_max_iterations_override"] = 9
    chat_state["loop_max_wait_seconds_override"] = 90.0
    second, _ = gateway_commands._queue_telegram_run(
        text="second loop request",
        chat_id="906",
        chat_key=chat_key,
        state=state,
        loop_config=base_loop_config,
    )

    assert first.max_iterations == 5
    assert first.max_wait_seconds == 30.0
    assert second.max_iterations == 9
    assert second.max_wait_seconds == 90.0


def test_queue_telegram_run_detects_workflow_prompt_mode() -> None:
    state = gateway_commands._default_gateway_state()
    job, reply = gateway_commands._queue_telegram_run(
        text="fermilink research plan a cavity qed benchmark",
        chat_id="905",
        chat_key="telegram:905",
        state=state,
    )
    assert job.mode == "research"
    assert job.prompt == "plan a cavity qed benchmark"
    assert job.max_iterations == 10
    assert job.max_wait_seconds == 6000.0
    assert "Execution mode: <code>research</code>." in reply


def test_handle_telegram_text_supports_workflow_prompts(
    tmp_path: Path,
) -> None:
    state = gateway_commands._default_gateway_state()
    workspaces_root = tmp_path / "workspaces"
    loop_calls: list[Path] = []
    exec_calls: list[Path] = []
    research_calls: list[tuple[Path, str, str | None]] = []
    reproduce_calls: list[tuple[Path, str, str | None]] = []

    def fake_repo_ensurer(repo_dir: Path, _init_git: bool) -> None:
        (repo_dir / "projects").mkdir(parents=True, exist_ok=True)
        (repo_dir / "projects" / "memory.md").write_text(
            (
                "# FermiLink Unified Memory\n\n"
                "### Plan\n"
                "- [x] Execute request\n\n"
                "### Key results\n"
                "- energy | value | -1.0 | test | projects/result.json\n"
            ),
            encoding="utf-8",
        )

    def fake_loop_runner(
        repo_dir: Path,
        _prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        loop_calls.append(repo_dir)
        return 0, {"status": "done", "reason": "done_token"}

    def fake_exec_runner(
        repo_dir: Path,
        _prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        exec_calls.append(repo_dir)
        return 0, {"status": "done", "reason": "exec_completed"}

    def fake_research_runner(
        repo_dir: Path,
        prompt: str,
        loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        research_calls.append((repo_dir, prompt, loop_config.hpc_profile))
        return 0, {"status": "done", "reason": "research_completed"}

    def fake_reproduce_runner(
        repo_dir: Path,
        prompt: str,
        loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        reproduce_calls.append((repo_dir, prompt, loop_config.hpc_profile))
        return 0, {"status": "done", "reason": "reproduce_completed"}

    chat_id = "904"
    chat_key = "telegram:904"
    loop_config = gateway_commands.GatewayLoopConfig(
        package_id=None,
        sandbox=None,
        codex_bin="codex",
        max_iterations=2,
        wait_seconds=0.0,
        max_wait_seconds=10.0,
        pid_stall_seconds=0.0,
        init_git=True,
        hpc_profile="scripts/hpc_profile_anvil.json",
    )

    gateway_commands._handle_telegram_text(
        text="/mode loop",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        research_runner=fake_research_runner,
        reproduce_runner=fake_reproduce_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    research_reply = gateway_commands._handle_telegram_text(
        text="fermilink research analyze cavity stability",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        research_runner=fake_research_runner,
        reproduce_runner=fake_reproduce_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    reproduce_reply = gateway_commands._handle_telegram_text(
        text="fermilink reproduce paper.md",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        exec_runner=fake_exec_runner,
        research_runner=fake_research_runner,
        reproduce_runner=fake_reproduce_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    assert len(loop_calls) == 0
    assert len(exec_calls) == 0
    assert len(research_calls) == 1
    assert len(reproduce_calls) == 1
    assert research_calls[0][1] == "analyze cavity stability"
    assert research_calls[0][2] == "scripts/hpc_profile_anvil.json"
    assert reproduce_calls[0][1] == "paper.md"
    assert reproduce_calls[0][2] == "scripts/hpc_profile_anvil.json"
    assert "Execution mode: <code>research</code>." in research_reply
    assert "Research workflow orchestration finished successfully." in research_reply
    assert "Execution mode: <code>reproduce</code>." in reproduce_reply
    assert "Reproduce workflow orchestration finished successfully." in reproduce_reply


def test_handle_telegram_text_reply_style_switches_to_agent(
    tmp_path: Path,
) -> None:
    state = gateway_commands._default_gateway_state()
    workspaces_root = tmp_path / "workspaces"

    def fake_repo_ensurer(repo_dir: Path, _init_git: bool) -> None:
        (repo_dir / "projects").mkdir(parents=True, exist_ok=True)
        (repo_dir / "projects" / "memory.md").write_text(
            (
                "# FermiLink Unified Memory\n\n"
                "### Plan\n"
                "- [x] Execute request\n\n"
                "### Key results\n"
                "- energy | value | -1.0 | test | projects/result.json\n"
            ),
            encoding="utf-8",
        )

    def fake_exec_runner(
        _repo_dir: Path,
        _prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        return 0, {
            "status": "done",
            "reason": "exec_completed",
            "agent_reply_raw": "Exact recommendation: use dt=0.05 for stability.",
            "agent_reply_text": "Exact recommendation: use dt=0.05 for stability.",
            "agent_reply_source": "exec_last_message",
            "agent_reply_exact": True,
        }

    chat_id = "901"
    chat_key = "telegram:901"
    loop_config = _loop_config()

    gateway_commands._handle_telegram_text(
        text="/mode exec",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    set_reply = gateway_commands._handle_telegram_text(
        text="/reply agent",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    run_reply = gateway_commands._handle_telegram_text(
        text="run once",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    query_reply = gateway_commands._handle_telegram_text(
        text="/reply",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    assert "Reply style set to agent." in set_reply
    assert "<b>Agent Reply</b>" in run_reply
    assert "Exact recommendation: use dt=0.05 for stability." in run_reply
    assert "Run complete in workspace" not in run_reply
    assert "Current reply style: agent" in query_reply


def test_gateway_defaults_to_exec_mode_and_agent_reply_style(
    tmp_path: Path,
) -> None:
    state = gateway_commands._default_gateway_state()
    workspaces_root = tmp_path / "workspaces"

    def fake_repo_ensurer(repo_dir: Path, _init_git: bool) -> None:
        (repo_dir / "projects").mkdir(parents=True, exist_ok=True)
        (repo_dir / "projects" / "memory.md").write_text(
            (
                "# FermiLink Unified Memory\n\n"
                "### Plan\n"
                "- [x] Execute request\n\n"
                "### Key results\n"
                "- energy | value | -1.0 | test | projects/result.json\n"
            ),
            encoding="utf-8",
        )

    def fake_exec_runner(
        _repo_dir: Path,
        _prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        return 0, {
            "status": "done",
            "reason": "exec_completed",
            "agent_reply_raw": "Exact recommendation: set cutoff to 8.0.",
            "agent_reply_text": "Exact recommendation: set cutoff to 8.0.",
            "agent_reply_source": "exec_last_message",
            "agent_reply_exact": True,
        }

    chat_id = "903"
    chat_key = "telegram:903"
    loop_config = _loop_config()

    mode_reply = gateway_commands._handle_telegram_text(
        text="/mode",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    reply_reply = gateway_commands._handle_telegram_text(
        text="/reply",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    run_reply = gateway_commands._handle_telegram_text(
        text="run with defaults",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        exec_runner=fake_exec_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    assert "Current mode: exec" in mode_reply
    assert "Current reply style: agent" in reply_reply
    assert "<b>Agent Reply</b>" in run_reply
    assert "Run complete in workspace" not in run_reply


def test_handle_telegram_text_reply_style_agent_falls_back_to_summary(
    tmp_path: Path,
) -> None:
    state = gateway_commands._default_gateway_state()
    workspaces_root = tmp_path / "workspaces"

    def fake_repo_ensurer(repo_dir: Path, _init_git: bool) -> None:
        (repo_dir / "projects").mkdir(parents=True, exist_ok=True)
        (repo_dir / "projects" / "memory.md").write_text(
            (
                "# FermiLink Unified Memory\n\n"
                "### Plan\n"
                "- [x] Execute request\n\n"
                "### Key results\n"
                "- energy | value | -1.0 | test | projects/result.json\n"
            ),
            encoding="utf-8",
        )

    def fake_loop_runner(
        _repo_dir: Path,
        _prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        return 0, {"status": "done", "reason": "done_token"}

    chat_id = "902"
    chat_key = "telegram:902"
    loop_config = _loop_config()
    gateway_commands._handle_telegram_text(
        text="/mode loop",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    gateway_commands._handle_telegram_text(
        text="/reply agent",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    run_reply = gateway_commands._handle_telegram_text(
        text="run loop",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    assert "<b>Agent Reply</b>" not in run_reply
    assert "Run complete in workspace" in run_reply


def test_status_reports_online_mode_workspace_and_last_run(tmp_path: Path) -> None:
    state = gateway_commands._default_gateway_state()
    workspaces_root = tmp_path / "workspaces"

    def fake_repo_ensurer(repo_dir: Path, _init_git: bool) -> None:
        (repo_dir / "projects").mkdir(parents=True, exist_ok=True)
        (repo_dir / "projects" / "memory.md").write_text(
            (
                "# FermiLink Unified Memory\n\n"
                "### Plan\n"
                "- [x] Run task\n\n"
                "### Key results\n"
                "- energy | value | -1.23 | test | projects/result.json\n"
            ),
            encoding="utf-8",
        )

    def fake_loop_runner(
        repo_dir: Path,
        _prompt: str,
        _loop_config: gateway_commands.GatewayLoopConfig,
    ) -> tuple[int, dict[str, object]]:
        return 0, {"status": "done", "reason": "done_token"}

    chat_id = "601"
    chat_key = "telegram:601"
    loop_config = _loop_config()
    gateway_commands._handle_telegram_text(
        text="/mode loop",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    before = gateway_commands._handle_telegram_text(
        text="/status",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    run_reply = gateway_commands._handle_telegram_text(
        text="run status test",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )
    after = gateway_commands._handle_telegram_text(
        text="/status",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
        workspaces_root=workspaces_root,
        loop_config=loop_config,
        loop_runner=fake_loop_runner,
        workspace_repo_ensurer=fake_repo_ensurer,
    )

    assert "<b>Gateway Status</b>" in before
    assert "No completed run recorded yet for this chat." in before
    assert "Execution mode: <code>loop</code>." in run_reply
    assert "Active workspace: <code>main</code>" in after
    assert "<b>Last Run</b>" in after
    assert "Status: <code>done</code>" in after
    assert "Reason: done token" in after
    assert "Started: <code>" in after
    assert "Finished: <code>" in after
    assert "(UTC" not in after


def test_status_reports_running_job_details_for_immediate_polling(
    tmp_path: Path,
) -> None:
    state = gateway_commands._default_gateway_state()
    telegram = gateway_commands._telegram_state(state)
    chat_id = "777"
    chat_key = "telegram:777"
    chat_state = gateway_commands._ensure_chat_state(telegram, chat_key)

    job, queued_reply = gateway_commands._queue_telegram_run(
        text="simulate h2o energy with pyscf",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
    )
    chat_state = gateway_commands._ensure_chat_state(telegram, chat_key)
    assert "Run queued and starting shortly." in queued_reply
    assert chat_state["pending_run_count"] == 1
    assert chat_state["is_running"] is False

    gateway_commands._mark_chat_job_running(chat_state, job)
    chat_state["current_run_mode"] = "loop 2/10"
    repo_dir = tmp_path / "repo"
    projects_dir = repo_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    (projects_dir / "memory.md").write_text(
        (
            "# FermiLink Unified Memory\n\n"
            "### Plan\n"
            "- [x] launch baseline run\n"
            "- [ ] verify field export consistency\n\n"
            "### Progress log\n"
            "- bootstrapped workspace and inputs\n"
            "- iterate mesh convergence in project script\n"
            "- patched parser for SCF stability reporting\n\n"
            "## Long-Term Memory (Persistent)\n"
            "### Key results\n"
            "- metric | value | 1.0 | test | projects/result.json\n"
        ),
        encoding="utf-8",
    )
    status = gateway_commands._build_status_message(chat_state, repo_dir=repo_dir)

    assert "Agent: <b>running</b>" in status
    assert "<b>Current Run</b>" in status
    assert status.count("Mode: <code>loop 2/10</code>") == 1
    assert "Latest progress:" in status
    assert "patched parser for SCF stability reporting" in status
    assert "Workspace:" not in status
    assert "<b>Last Run</b>" not in status
    assert "Thinking:" not in status
    assert "Prompt: simulate h2o energy with pyscf" in status
    assert "(UTC" not in status


def test_status_reports_workflow_task_progress_with_totals() -> None:
    state = gateway_commands._default_gateway_state()
    telegram = gateway_commands._telegram_state(state)
    chat_id = "778"
    chat_key = "telegram:778"
    chat_state = gateway_commands._ensure_chat_state(telegram, chat_key)

    job, _ = gateway_commands._queue_telegram_run(
        text="fermilink research benchmark cavity",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
    )
    gateway_commands._mark_chat_job_running(chat_state, job)
    chat_state["current_run_mode"] = "research task 2/5 loop 3/10"

    status = gateway_commands._build_status_message(chat_state)

    assert "Agent: <b>running</b>" in status
    assert "Mode: <code>research task 2/5 loop 3/10</code>" in status
    assert "<b>Current Run</b>" in status
    assert "<b>Last Run</b>" not in status


def test_status_hides_thinking_line_even_when_progress_log_exists(
    tmp_path: Path,
) -> None:
    state = gateway_commands._default_gateway_state()
    telegram = gateway_commands._telegram_state(state)
    chat_id = "779"
    chat_key = "telegram:779"
    chat_state = gateway_commands._ensure_chat_state(telegram, chat_key)

    job, _ = gateway_commands._queue_telegram_run(
        text="track heading boundary behavior",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
    )
    gateway_commands._mark_chat_job_running(chat_state, job)
    repo_dir = tmp_path / "repo"
    projects_dir = repo_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    (projects_dir / "memory.md").write_text(
        (
            "# FermiLink Unified Memory\n\n"
            "### Progress log\n"
            "- finished mesh sweep for cavity mode\n"
            "## Long-Term Memory (Persistent)\n"
            "- this line must never be parsed as progress\n"
        ),
        encoding="utf-8",
    )

    status = gateway_commands._build_status_message(chat_state, repo_dir=repo_dir)

    assert "Thinking:" not in status
    assert "Latest progress:" in status
    assert "finished mesh sweep for cavity mode" in status
    assert "Long-Term Memory (Persistent)" not in status
    assert "this line must never be parsed as progress" not in status


def test_status_latest_progress_formats_timestamp_and_markdown(tmp_path: Path) -> None:
    state = gateway_commands._default_gateway_state()
    telegram = gateway_commands._telegram_state(state)
    chat_state = gateway_commands._ensure_chat_state(telegram, "telegram:780")
    repo_dir = tmp_path / "repo"
    projects_dir = repo_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    (projects_dir / "memory.md").write_text(
        (
            "# FermiLink Unified Memory\n\n"
            "### Progress log\n"
            "- initialized\n"
            "- 2026-02-23T12:42:11Z: Completed "
            "`projects/2026-02-23-hcn-bragg-rttddft-rescaling-sweep-x2-x32/summary.md` "
            "with sweep checks.\n"
        ),
        encoding="utf-8",
    )

    status = gateway_commands._build_status_message(chat_state, repo_dir=repo_dir)
    expected_local = gateway_commands._format_local_timestamp("2026-02-23T12:42:11Z")

    assert "Latest progress:" in status
    assert expected_local in status
    assert "2026-02-23T12:42:11Z" not in status
    assert (
        "<code>projects/2026-02-23-hcn-bragg-rttddft-rescaling-sweep-x2-x32/summary.md</code>"
        in status
    )


def test_status_reports_queued_when_requests_waiting() -> None:
    state = gateway_commands._default_gateway_state()
    chat_id = "778"
    chat_key = "telegram:778"

    _, first = gateway_commands._queue_telegram_run(
        text="first run",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
    )
    _, second = gateway_commands._queue_telegram_run(
        text="second run",
        chat_id=chat_id,
        chat_key=chat_key,
        state=state,
    )

    telegram = gateway_commands._telegram_state(state)
    chat_state = gateway_commands._ensure_chat_state(telegram, chat_key)
    status = gateway_commands._build_status_message(chat_state)

    assert "Run queued and starting shortly." in first
    assert "Queued request in workspace" in second
    assert "Queue position: <code>2</code>" in second
    assert "Agent: <b>queued</b> (2 pending)" in status
    assert "<b>Current Run</b>" not in status


def test_collect_media_for_run_reply_prefers_memory_and_recent_files(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    projects_dir = repo_dir / "projects"
    outputs_dir = repo_dir / "outputs"
    projects_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    key_image = projects_dir / "scf_convergence.png"
    key_image.write_bytes(b"png")
    key_doc = projects_dir / "report.pdf"
    key_doc.write_bytes(b"pdf")
    recent_image = outputs_dir / "recent.png"
    recent_image.write_bytes(b"png")

    (projects_dir / "memory.md").write_text(
        (
            "# FermiLink Unified Memory\n\n"
            "### Key results\n"
            "- k1 | SCF convergence figure | generated | run | projects/scf_convergence.png\n"
            "- k2 | report | generated | run | projects/report.pdf\n"
        ),
        encoding="utf-8",
    )

    now = time.time()
    old = now - 120
    newer = now - 1
    # Keep key artifacts old to validate memory-evidence inclusion.
    key_image.touch()
    key_doc.touch()
    recent_image.touch()
    key_image_mtime = old
    key_doc_mtime = old
    recent_mtime = newer
    import os

    os.utime(key_image, (key_image_mtime, key_image_mtime))
    os.utime(key_doc, (key_doc_mtime, key_doc_mtime))
    os.utime(recent_image, (recent_mtime, recent_mtime))

    images, docs = gateway_commands._collect_media_for_run_reply(
        repo_dir, run_started_epoch=now - 10
    )
    assert key_image in images
    assert recent_image in images
    assert key_doc in docs


def test_split_key_result_item_handles_labeled_values_with_pipe_markers() -> None:
    item = (
        "result_id: run2_ez | metric: final Ez spatial field | "
        "value: shape 80x80, max |Ez| | conditions: 2d bragg | "
        "evidence_path: projects/ez_field_final.png"
    )
    result_id, metric, value, conditions, evidence_path = (
        gateway_commands._split_key_result_item(item)
    )
    assert result_id == "run2_ez"
    assert metric == "final Ez spatial field"
    assert "shape 80x80, max" in value
    assert "Ez" in value
    assert conditions == "2d bragg"
    assert evidence_path == "projects/ez_field_final.png"


def test_run_summary_uses_only_last_key_finding_entry(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    projects_dir = repo_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    (projects_dir / "memory.md").write_text(
        (
            "# FermiLink Unified Memory\n\n"
            "### Key results\n"
            "- result_id: run1_ez | metric: final Ez spatial field | "
            "value: shape 80x80, max |Ez| | conditions: old run | "
            "evidence_path: projects/old_ez.png\n"
            "- result_id: run1_pe | metric: Pe(t) | value: Pe_final=3.059e-4 | "
            "conditions: old run | evidence_path: projects/old_pe.csv\n"
            "- result_id: run2_ez | metric: final Ez spatial field | "
            "value: shape 80, max |Ez| | conditions: latest run | "
            "evidence_path: projects/new_ez.png\n"
            "- result_id: run2_pe | metric: Pe(t) | value: Pe_final=4.553e-4 | "
            "conditions: latest run | evidence_path: projects/new_pe.csv\n"
        ),
        encoding="utf-8",
    )

    summary = gateway_commands._build_run_summary_message(
        mode="exec",
        workspace={"id": "mxl", "label": "mxl"},
        repo_dir=repo_dir,
        code=0,
        outcome={"status": "done", "reason": "exec_completed"},
    )

    assert "final Ez spatial field:" not in summary
    assert summary.count("Pe(t):") == 1
    assert "latest run" in summary
    assert "old run" not in summary


def test_run_summary_includes_parameter_source_and_uncertainty_sections(
    tmp_path: Path,
) -> None:
    repo_dir = tmp_path / "repo"
    projects_dir = repo_dir / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    (projects_dir / "memory.md").write_text(
        (
            "# FermiLink Unified Memory\n\n"
            "### Key results\n"
            "- result_id: r1 | metric: splitting | value: 5210 cm^-1 | "
            "conditions: x8 | evidence_path: projects/result.json\n\n"
            "### Parameter source mapping\n"
            "- run_03 | dt_fs | 0.02 | project README defaults | "
            "projects/input.yaml | stable in x4 sweep\n"
            "- run_id: run_04 | parameter_or_setting: kappa | "
            "value: max |E| threshold | source: sweep script defaults | "
            "evidence_path: projects/sweep.json | notes: tune after x16 check\n\n"
            "### Simulation uncertainty\n"
            "- run_03 | unresolved basis-set sensitivity | shifts splitting by ~5% | "
            "run higher basis sanity check | open\n"
            "- run_id: run_04 | uncertainty_or_assumption: detector window clipping | "
            "impact: may clip |Omega| peaks | "
            "mitigation_or_next_step: widen window and refit | status: planned\n"
        ),
        encoding="utf-8",
    )

    summary = gateway_commands._build_run_summary_message(
        mode="loop",
        workspace={"id": "mxl", "label": "mxl"},
        repo_dir=repo_dir,
        code=0,
        outcome={"status": "done", "reason": "done_token"},
    )

    assert "<b>Parameter Source Mapping</b>" in summary
    assert "[run_04] kappa:" in summary
    assert "source: sweep script defaults" in summary
    assert "evidence: projects/sweep.json" in summary
    assert "[run_03] dt_fs: 0.02" not in summary
    assert "<b>Simulation Uncertainty</b>" in summary
    assert "[run_04] detector window clipping" in summary
    assert "impact: may clip" in summary
    assert "Omega" in summary
    assert "peaks" in summary
    assert "next step: widen window and refit" in summary
    assert "status: planned" in summary
    assert "[run_03] unresolved basis-set sensitivity" not in summary
