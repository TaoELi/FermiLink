from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

import pytest

from fermilink import package_registry
from fermilink.package_registry import PackageError, install_from_zip, load_registry


class _FakeResponse:
    def __init__(self, chunks: list[bytes], *, headers: dict[str, str] | None = None) -> None:
        self._chunks = list(chunks)
        self.headers = headers or {}

    def read(self, _size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        _ = (exc_type, exc, tb)
        return False


class _TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_download_zip_enforces_max_size(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    destination = tmp_path / "package.zip"

    monkeypatch.setattr(
        package_registry.urllib.request,
        "urlopen",
        lambda _req: _FakeResponse([b"abcdef", b""]),
    )

    with pytest.raises(PackageError, match="exceeded max size"):
        package_registry._download_zip(
            "https://example.invalid/package.zip",
            destination,
            max_bytes=5,
        )


def test_download_zip_shows_progress_bar_when_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "package.zip"
    stderr_buffer = _TTYBuffer()

    monkeypatch.setenv("FERMILINK_PROGRESS", "1")
    monkeypatch.delenv("FERMILINK_NO_PROGRESS", raising=False)
    monkeypatch.setattr(
        package_registry.urllib.request,
        "urlopen",
        lambda _req: _FakeResponse(
            [b"12345", b"67890", b""],
            headers={"Content-Length": "10"},
        ),
    )
    monkeypatch.setattr(package_registry.sys, "stderr", stderr_buffer)

    written = package_registry._download_zip(
        "https://example.invalid/package.zip",
        destination,
        max_bytes=100,
    )
    assert written == 10
    output = stderr_buffer.getvalue()
    assert "Downloading [" in output
    assert "100.00%" in output
    assert output.endswith("\n")


def test_download_zip_hides_progress_bar_when_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "package.zip"
    stderr_buffer = _TTYBuffer()

    monkeypatch.setenv("FERMILINK_PROGRESS", "1")
    monkeypatch.setenv("FERMILINK_NO_PROGRESS", "1")
    monkeypatch.setattr(
        package_registry.urllib.request,
        "urlopen",
        lambda _req: _FakeResponse([b"abc", b""]),
    )
    monkeypatch.setattr(package_registry.sys, "stderr", stderr_buffer)

    written = package_registry._download_zip(
        "https://example.invalid/package.zip",
        destination,
        max_bytes=100,
    )
    assert written == 3
    assert stderr_buffer.getvalue() == ""


def test_download_zip_shows_running_dots_when_content_length_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "package.zip"
    stderr_buffer = _TTYBuffer()

    monkeypatch.setenv("FERMILINK_PROGRESS", "1")
    monkeypatch.delenv("FERMILINK_NO_PROGRESS", raising=False)
    monkeypatch.setattr(
        package_registry.urllib.request,
        "urlopen",
        lambda _req: _FakeResponse([b"abcdef", b""]),
    )
    monkeypatch.setattr(package_registry.sys, "stderr", stderr_buffer)

    written = package_registry._download_zip(
        "https://example.invalid/package.zip",
        destination,
        max_bytes=100,
    )
    assert written == 6
    output = stderr_buffer.getvalue()
    assert "Downloading." in output
    assert "[" not in output
    assert output.endswith("\n")


def test_safe_extract_zip_rejects_unsafe_paths(tmp_path: Path) -> None:
    zip_path = tmp_path / "unsafe.zip"
    extract_root = tmp_path / "extract"
    extract_root.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr("../evil.txt", "bad")

    with pytest.raises(PackageError, match="unsafe path"):
        package_registry._safe_extract_zip(zip_path, extract_root)


def test_install_from_zip_force_overwrites_existing_package(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    target_dir = scipkg_root / "packages" / "ase"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "old.txt").write_text("old", encoding="utf-8")

    payload_zip = tmp_path / "payload.zip"
    with zipfile.ZipFile(payload_zip, "w") as archive:
        archive.writestr("ase-main/skills/README.md", "new-skills")
        archive.writestr("ase-main/projects/stale.txt", "stale")

    def _fake_download(_url: str, destination: Path, _max_bytes: int) -> int:
        shutil.copy2(payload_zip, destination)
        return destination.stat().st_size

    monkeypatch.setattr(package_registry, "_download_zip", _fake_download)

    with pytest.raises(PackageError, match="already exists"):
        install_from_zip(
            scipkg_root,
            "ase",
            zip_url="https://example.invalid/ase.zip",
            force=False,
        )

    meta = install_from_zip(
        scipkg_root,
        "ase",
        zip_url="https://example.invalid/ase.zip",
        force=True,
        activate=True,
    )

    assert meta["id"] == "ase"
    assert (target_dir / "skills" / "README.md").exists()
    assert not (target_dir / "old.txt").exists()
    assert not (target_dir / "projects").exists()
    assert "projects" in meta.get("removed_root_directories", [])

    registry = load_registry(scipkg_root)
    assert registry["active_package"] == "ase"
