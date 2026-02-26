from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_docs_conf_module():
    conf_path = Path(__file__).resolve().parents[1] / "docs" / "source" / "conf.py"
    spec = importlib.util.spec_from_file_location("fermilink_docs_conf", conf_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generate_built_in_scientific_packages_page(tmp_path: Path) -> None:
    docs_conf = _load_docs_conf_module()
    channel_path = tmp_path / "skilled-scipkg.json"
    output_path = tmp_path / "built_in_scientific_packages.rst"
    payload = {
        "channel_id": "skilled-scipkg",
        "updated_at": "2026-02-26T00:00:00Z",
        "packages": [
            {
                "package_id": "beta",
                "title": "Beta Package",
                "zip_url": "https://github.com/org-b/repo-b/archive/refs/heads/main.zip",
            },
            {
                "package_id": "alpha",
                "title": "Alpha Package",
                "zip_url": "https://github.com/org-a/repo-a/archive/refs/heads/dev.zip",
            },
        ],
    }
    channel_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    docs_conf._generate_built_in_scientific_packages_page(
        channel_path=channel_path,
        output_path=output_path,
    )

    rendered = output_path.read_text(encoding="utf-8")
    assert "Built-in Supported Scientific Packages" in rendered
    assert "- Last curated update: ``2026-02-25 19:00:00 EST``" in rendered
    assert "- Total built-in packages: ``2``" in rendered
    assert "``alpha``" in rendered
    assert "``beta``" in rendered
    assert rendered.index("``alpha``") < rendered.index("``beta``")
    assert "`org-a/repo-a <https://github.com/org-a/repo-a>`_" in rendered
    assert "`org-b/repo-b <https://github.com/org-b/repo-b>`_" in rendered


def test_github_repo_url_from_zip_url_falls_back_when_not_github() -> None:
    docs_conf = _load_docs_conf_module()
    source_url = "https://example.invalid/org/repo/archive/main.zip"
    assert docs_conf._github_repo_url_from_zip_url(source_url) == source_url


def test_format_updated_at_est_falls_back_when_invalid() -> None:
    docs_conf = _load_docs_conf_module()
    source_value = "not-a-valid-time"
    assert docs_conf._format_updated_at_est(source_value) == source_value
