import json
import os
import subprocess
import time
from pathlib import Path


def _load_config() -> dict:
    config_path = Path(__file__).resolve().with_name("config.json")
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _wait_for_host_port(path: Path, timeout_s: int) -> tuple[str, int]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) >= 2 and lines[0].strip() and lines[1].strip():
                return lines[0].strip(), int(lines[1].strip())
        time.sleep(1.0)
    raise FileNotFoundError(f"Host/port file not found or incomplete: {path}")


def _format_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + " ".join(str(v) for v in value) + "]"
    return str(value)


def _build_param_string(cfg: dict) -> str:
    ordered_keys = [
        "molecule_xyz",
        "functional",
        "dft_grid_name",
        "basis",
        "dt_rttddft_au",
        "memory",
        "num_threads",
        "force_type",
        "n_fock_per_nuc",
        "n_elec_per_fock",
        "partial_charges",
        "checkpoint",
        "restart",
    ]

    tokens = []
    used = set()
    for key in ordered_keys:
        if key in cfg:
            tokens.append(f"{key}={_format_value(cfg[key])}")
            used.add(key)

    # Pass through additional model parameters if present.
    for key in sorted(k for k in cfg.keys() if k not in used):
        tokens.append(f"{key}={_format_value(cfg[key])}")

    return ", ".join(tokens)


def _launch_clients(host: str, port: int, param: str, n_clients: int) -> None:
    if n_clients <= 0:
        raise ValueError("n_clients must be positive")

    base_cmd = [
        "mxl_driver",
        "--model",
        "rtehrenfest",
        "--address",
        host,
        "--port",
        str(port),
        "--param",
        param,
    ]

    if n_clients == 1:
        subprocess.check_call(base_cmd + ["--verbose"])
        return

    procs = []
    for _ in range(n_clients - 1):
        procs.append(subprocess.Popen(base_cmd))

    # Keep one client in foreground so SLURM tracks job status correctly.
    subprocess.check_call(base_cmd)

    failed = 0
    for proc in procs:
        rc = proc.wait()
        if rc != 0:
            failed += 1
    if failed:
        raise RuntimeError(f"{failed} background rtehrenfest client(s) failed")


def main() -> None:
    cfg = _load_config()
    run_cfg = cfg["run"]

    if not bool(run_cfg.get("include_molecules", True)):
        print("include_molecules is false; no driver clients are required.")
        return

    host_port_file = Path(__file__).resolve().with_name(str(cfg["host_port_file"]))
    timeout_s = int(cfg.get("driver_launch", {}).get("wait_timeout_s", 180))
    host, port = _wait_for_host_port(host_port_file, timeout_s=timeout_s)

    rte_cfg = dict(cfg["rtehrenfest"])
    # Resolve molecule file relative to this project directory.
    if "molecule_xyz" in rte_cfg:
        rel = Path(str(rte_cfg["molecule_xyz"]))
        rte_cfg["molecule_xyz"] = str((Path(__file__).resolve().parent / rel).resolve())

    param = _build_param_string(rte_cfg)

    launch_cfg = cfg.get("driver_launch", {})
    default_clients = int(launch_cfg.get("clients_per_job", 1))
    n_clients = int(os.environ.get("MXL_DRIVER_CLIENTS_THIS_JOB", default_clients))

    print(f"Launching {n_clients} rtehrenfest client(s) to {host}:{port}")
    _launch_clients(host=host, port=port, param=param, n_clients=n_clients)


if __name__ == "__main__":
    main()

