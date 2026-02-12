from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChannelPackage:
    package_id: str
    zip_url: str
    title: str


TEL_RESEARCH_GROUP_PACKAGES: dict[str, ChannelPackage] = {
    "maxwelllink": ChannelPackage(
        package_id="maxwelllink",
        title="MaxwellLink",
        zip_url="https://github.com/TEL-Research-Group/MaxwellLink/archive/refs/heads/main.zip",
    ),
    "meep": ChannelPackage(
        package_id="meep",
        title="Meep",
        zip_url="https://github.com/TEL-Research-Group/meep/archive/refs/heads/master.zip",
    ),
    "lammps": ChannelPackage(
        package_id="lammps",
        title="LAMMPS",
        zip_url="https://github.com/TEL-Research-Group/lammps/archive/refs/heads/master.zip",
    ),
    "qutip": ChannelPackage(
        package_id="qutip",
        title="QuTiP",
        zip_url="https://github.com/TEL-Research-Group/qutip/archive/refs/heads/master.zip",
    ),
    "psi4": ChannelPackage(
        package_id="psi4",
        title="Psi4",
        zip_url="https://github.com/TEL-Research-Group/psi4/archive/refs/heads/master.zip",
    ),
    "ase": ChannelPackage(
        package_id="ase",
        title="ASE",
        zip_url="https://github.com/TEL-Research-Group/ase/archive/refs/heads/master.zip",
    ),
    "oqupy": ChannelPackage(
        package_id="oqupy",
        title="OQuPy",
        zip_url="https://github.com/TEL-Research-Group/OQuPy/archive/refs/heads/main.zip",
    ),
    "packmol": ChannelPackage(
        package_id="packmol",
        title="Packmol",
        zip_url="https://github.com/TEL-Research-Group/packmol/archive/refs/heads/master.zip",
    ),
    "kwant": ChannelPackage(
        package_id="kwant",
        title="Kwant",
        zip_url="https://github.com/TEL-Research-Group/kwant/archive/refs/heads/master.zip",
    ),
    "tkwant": ChannelPackage(
        package_id="tkwant",
        title="TKwant",
        zip_url="https://github.com/TEL-Research-Group/tkwant/archive/refs/heads/master.zip",
    ),
    "elk": ChannelPackage(
        package_id="elk",
        title="ELK",
        zip_url="https://github.com/TEL-Research-Group/elk/archive/refs/heads/main.zip",
    ),
}

CHANNELS: dict[str, dict[str, ChannelPackage]] = {
    "tel-research-group": TEL_RESEARCH_GROUP_PACKAGES,
}

CHANNEL_ALIASES = {
    "tel": "tel-research-group",
    "tel-research-group": "tel-research-group",
    "tle-research-group": "tel-research-group",
}


def normalize_channel_id(channel: str | None) -> str:
    value = (channel or "tel-research-group").strip().lower()
    return CHANNEL_ALIASES.get(value, value)


def resolve_curated_package(package_id: str, *, channel: str | None = None) -> ChannelPackage:
    normalized_channel = normalize_channel_id(channel)
    packages = CHANNELS.get(normalized_channel)
    if packages is None:
        valid = ", ".join(sorted(CHANNELS.keys()))
        raise ValueError(f"Unknown channel '{normalized_channel}'. Available channels: {valid}")

    payload = packages.get(package_id)
    if payload is None:
        supported = ", ".join(sorted(packages.keys()))
        raise ValueError(
            f"Package '{package_id}' is not published in channel '{normalized_channel}'. Supported packages: {supported}"
        )
    return payload
