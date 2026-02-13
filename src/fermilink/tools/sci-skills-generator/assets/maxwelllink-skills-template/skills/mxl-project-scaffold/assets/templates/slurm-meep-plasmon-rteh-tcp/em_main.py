import json
import math
import os
import socket
from pathlib import Path

import meep as mp
import numpy as np
import maxwelllink as mxl
from maxwelllink import sockets as mxs


def _load_config() -> dict:
    config_path = Path(__file__).resolve().with_name("config.json")
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _pick_free_port(bind_addr: str = "0.0.0.0") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((bind_addr, 0))
        return int(s.getsockname()[1])


def _write_host_port(path: Path, host: str, port: int) -> None:
    if mxs.am_master():
        path.write_text(f"{host}\n{port}\n", encoding="utf-8")


def _build_pt_medium() -> mp.Medium:
    # Pt model from A.D. Rakic et al., Applied Optics 37, 5271 (1998)
    eV_um_scale = 1.0 / 1.23984193

    plasma = 9.59 * eV_um_scale

    f0 = 0.333
    frq0 = 1e-10
    gam0 = 0.080 * eV_um_scale
    sig0 = f0 * plasma**2 / frq0**2

    f1 = 0.191
    frq1 = 0.780 * eV_um_scale
    gam1 = 0.517 * eV_um_scale
    sig1 = f1 * plasma**2 / frq1**2

    f2 = 0.659
    frq2 = 1.314 * eV_um_scale
    gam2 = 1.838 * eV_um_scale
    sig2 = f2 * plasma**2 / frq2**2

    susceptibilities = [
        mp.DrudeSusceptibility(frequency=frq0, gamma=gam0, sigma=sig0),
        mp.LorentzianSusceptibility(frequency=frq1, gamma=gam1, sigma=sig1),
        mp.LorentzianSusceptibility(frequency=frq2, gamma=gam2, sigma=sig2),
    ]
    return mp.Medium(epsilon=1.0, E_susceptibilities=susceptibilities)


def _pw_amp(k_vec: mp.Vector3, origin: mp.Vector3, amplitude: float):
    def _amp(x: mp.Vector3):
        phase = 2.0 * math.pi * k_vec.dot(x + origin)
        return amplitude * complex(math.cos(phase), math.sin(phase))

    return _amp


def _save_molecule_histories(molecules: list[mxl.Molecule]) -> None:
    for idx, molecule in enumerate(molecules):
        rows = molecule.additional_data_history
        payload = {
            "time_au": np.array([float(ad.get("time_au", 0.0)) for ad in rows], dtype=float),
            "energy_au": np.array([float(ad.get("energy_au", 0.0)) for ad in rows], dtype=float),
            "mux_au": np.array([float(ad.get("mux_au", 0.0)) for ad in rows], dtype=float),
            "muy_au": np.array([float(ad.get("muy_au", 0.0)) for ad in rows], dtype=float),
            "muz_au": np.array([float(ad.get("muz_au", 0.0)) for ad in rows], dtype=float),
        }
        np.savez(f"mol_{idx}_data.npz", **payload)


def main() -> None:
    cfg = _load_config()

    run_cfg = cfg["run"]
    units_cfg = cfg["units"]
    meep_cfg = cfg["meep"]
    mol_cfg = cfg["molecule"]

    include_dielectric = bool(run_cfg.get("include_dielectric", True))
    include_molecules = bool(run_cfg.get("include_molecules", True))

    a = float(run_cfg["lattice_a_um"])
    r = a * float(run_cfg["rod_radius_fraction"])
    nmol = int(run_cfg["nmol"])

    resolution = int(meep_cfg["resolution"])
    tabs = float(meep_cfg["tabs_um"])
    tair = float(meep_cfg["tair_um"])
    h = float(meep_cfg["h_um"])
    tmet = float(meep_cfg["tmet_um"])
    tsub = float(meep_cfg["tsub_um"])

    sz = tabs + tair + h + tmet + tsub + tabs
    cell_size = mp.Vector3(a, a, sz)

    pml_layers = [
        mp.PML(thickness=tabs, direction=mp.Z, side=mp.High),
        mp.Absorber(thickness=tabs, direction=mp.Z, side=mp.Low),
    ]

    lmin = float(meep_cfg["wavelength_min_um"])
    lmax = float(meep_cfg["wavelength_max_um"])
    fmin = 1.0 / lmax
    fmax = 1.0 / lmin
    fcen = 0.5 * (fmin + fmax)
    df = fmax - fmin

    theta = math.radians(float(run_cfg.get("theta_deg", 0.0)))
    k_point = mp.Vector3(math.sin(theta), 0.0, math.cos(theta)).scale(fcen)

    src_pos = 0.5 * sz - tabs - 0.2 * tair
    source_amplitude = float(meep_cfg.get("source_amplitude", 5e4))
    sources = [
        mp.Source(
            mp.GaussianSource(fcen, fwidth=df),
            component=mp.Ey,
            center=mp.Vector3(0.0, 0.0, src_pos),
            size=mp.Vector3(a, a, 0.0),
            amp_func=_pw_amp(k_point, mp.Vector3(0.0, 0.0, src_pos), source_amplitude),
        )
    ]

    geometry = []
    if include_dielectric:
        pt = _build_pt_medium()
        si = mp.Medium(index=3.5)
        geometry = [
            mp.Cylinder(
                material=pt,
                radius=r,
                height=h,
                center=mp.Vector3(0.0, 0.0, 0.5 * sz - tabs - tair - 0.5 * h),
            ),
            mp.Block(
                material=pt,
                size=mp.Vector3(mp.inf, mp.inf, tmet),
                center=mp.Vector3(0.0, 0.0, 0.5 * sz - tabs - tair - h - 0.5 * tmet),
            ),
            mp.Block(
                material=si,
                size=mp.Vector3(mp.inf, mp.inf, tsub + tabs),
                center=mp.Vector3(0.0, 0.0, 0.5 * sz - tabs - tair - h - tmet - 0.5 * (tsub + tabs)),
            ),
        ]

    molecules = []
    hub = None

    if include_molecules:
        port = _pick_free_port("0.0.0.0")
        host_for_clients = os.environ.get("MXL_HOST", socket.gethostname())
        host_port_file = Path(__file__).resolve().with_name(str(cfg["host_port_file"]))
        _write_host_port(host_port_file, host_for_clients, port)

        hub = mxl.SocketHub(
            host="",
            port=port,
            timeout=200.0,
            latency=1e-4,
        )
        print(f"Hub listening on {host_for_clients}:{port}")

        # Place molecules on a square lattice above the plasmonic cylinder.
        nmol_per_dim = max(1, int(math.ceil(math.sqrt(nmol))))
        len_molecule = float(mol_cfg["cube_length_um"])
        sigma = len_molecule * float(mol_cfg["sigma_fraction"])

        z_top_of_cylinder = 0.5 * sz - tabs - tair
        x_left = -0.5 * a + len_molecule
        x_right = 0.5 * a - len_molecule
        y_left = -0.5 * a + len_molecule
        y_right = 0.5 * a - len_molecule

        for ix in range(nmol_per_dim):
            for iy in range(nmol_per_dim):
                if len(molecules) >= nmol:
                    break

                x_pos = 0.0
                y_pos = 0.0
                if nmol_per_dim > 1:
                    x_pos = x_left + ix * (x_right - x_left) / (nmol_per_dim - 1)
                    y_pos = y_left + iy * (y_right - y_left) / (nmol_per_dim - 1)

                molecules.append(
                    mxl.Molecule(
                        hub=hub,
                        center=mp.Vector3(x_pos, y_pos, z_top_of_cylinder + 0.5 * len_molecule),
                        size=mp.Vector3(len_molecule, len_molecule, len_molecule),
                        sigma=sigma,
                        dimensions=int(mol_cfg.get("dimensions", 3)),
                        rescaling_factor=float(mol_cfg.get("rescaling_factor", 1.0)),
                    )
                )

    if include_molecules:
        sim = mxl.MeepSimulation(
            hub=hub,
            molecules=molecules,
            time_units_fs=float(units_cfg["time_units_fs"]),
            cell_size=cell_size,
            geometry=geometry,
            sources=sources,
            boundary_layers=pml_layers,
            k_point=k_point,
            resolution=resolution,
        )
    else:
        sim = mp.Simulation(
            cell_size=cell_size,
            geometry=geometry,
            sources=sources,
            boundary_layers=pml_layers,
            k_point=k_point,
            resolution=resolution,
        )

    sim.run(until=float(run_cfg["until"]))

    if include_molecules and bool(run_cfg.get("save_molecule_npz", True)) and mp.am_master():
        _save_molecule_histories(molecules)


if __name__ == "__main__":
    main()

