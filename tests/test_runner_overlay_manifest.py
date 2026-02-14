from __future__ import annotations

from pathlib import Path

from fermilink.runner import scientific_packages as scipkg


def _make_package(path: Path, entries: list[str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        entry_dir = path / entry
        entry_dir.mkdir(parents=True, exist_ok=True)
        (entry_dir / "README.md").write_text(f"{entry}\n", encoding="utf-8")


def test_overlay_manifest_cleans_stale_entries_and_dependencies(tmp_path: Path) -> None:
    scipkg_root = tmp_path / "scientific_packages"
    workspace_root = tmp_path / "workspaces" / "session-1"
    repo_dir = workspace_root / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    main_root = tmp_path / "pkg-main"
    dep_root = tmp_path / "pkg-dependency"
    alt_root = tmp_path / "pkg-alt"
    _make_package(main_root, ["skills", "docs"])
    _make_package(dep_root, ["meep"])
    _make_package(alt_root, ["models"])

    scipkg.register_package(
        scipkg_root,
        "mainpkg",
        installed_path=main_root,
        source="local-path:main",
        activate=True,
    )
    scipkg.register_package(
        scipkg_root,
        "deppkg",
        installed_path=dep_root,
        source="local-path:dep",
    )
    scipkg.register_package(
        scipkg_root,
        "altpkg",
        installed_path=alt_root,
        source="local-path:alt",
    )

    scipkg.set_package_overlay_entries(scipkg_root, "mainpkg", ["skills"])
    scipkg.set_package_dependency_ids(scipkg_root, "mainpkg", ["deppkg"])
    main_meta = scipkg.load_registry(scipkg_root)["packages"]["mainpkg"]

    first = scipkg.overlay_package_into_repo(
        repo_dir=repo_dir,
        workspace_root=workspace_root,
        package_id="mainpkg",
        package_meta=main_meta,
        scipkg_root=scipkg_root,
    )
    assert first["linked_count"] == 1
    assert (repo_dir / "skills").is_symlink()
    dep_link = repo_dir / scipkg.PACKAGE_DEPENDENCIES_DIRNAME / "deppkg"
    assert dep_link.is_symlink()

    scipkg.set_package_overlay_entries(scipkg_root, "mainpkg", ["docs"])
    scipkg.set_package_dependency_ids(scipkg_root, "mainpkg", None)
    updated_main_meta = scipkg.load_registry(scipkg_root)["packages"]["mainpkg"]
    second = scipkg.overlay_package_into_repo(
        repo_dir=repo_dir,
        workspace_root=workspace_root,
        package_id="mainpkg",
        package_meta=updated_main_meta,
        scipkg_root=scipkg_root,
    )
    assert second["linked_count"] == 1
    assert (repo_dir / "docs").is_symlink()
    assert not (repo_dir / "skills").exists()
    assert not dep_link.exists()
    assert not (repo_dir / scipkg.PACKAGE_DEPENDENCIES_DIRNAME).exists()

    alt_meta = scipkg.load_registry(scipkg_root)["packages"]["altpkg"]
    third = scipkg.overlay_package_into_repo(
        repo_dir=repo_dir,
        workspace_root=workspace_root,
        package_id="altpkg",
        package_meta=alt_meta,
        scipkg_root=scipkg_root,
    )
    assert third["linked_count"] == 1
    assert (repo_dir / "models").is_symlink()
    assert not (repo_dir / "docs").exists()

    manifest = scipkg.load_workspace_manifest(workspace_root)
    assert manifest is not None
    assert manifest["package_id"] == "altpkg"


def test_remove_managed_symlinks_removes_copy_mode_entries(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    copied_entry = repo_dir / "public"
    copied_entry.mkdir(parents=True, exist_ok=True)
    (copied_entry / "index.html").write_text("copied", encoding="utf-8")

    manifest = {
        "linked_entries": [
            {
                "name": "public",
                "mode": "copy",
                "source": str((tmp_path / "src").resolve()),
            }
        ]
    }

    scipkg._remove_managed_symlinks(repo_dir, manifest, only_names={"public"})

    assert not copied_entry.exists()
