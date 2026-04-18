from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
from urllib.parse import urlparse

# --- Make the package importable ---
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

CURATED_SCIPKG_CHANNEL = (
    ROOT / "src" / "fermilink" / "data" / "curated_channels" / "skilled-scipkg.json"
)
BUILTIN_SCIPKG_PAGE = ROOT / "docs" / "source" / "built_in_scientific_packages.rst"
EST_TZ = timezone(timedelta(hours=-5), name="EST")

# --- Project info ---
project = "FermiLink"
author = "Tao E. Li"
try:
    release = pkg_version("fermilink")
    version = ".".join(release.split(".")[:2])
except PackageNotFoundError:
    release = version = "0.2.0"

# Keep the sidebar title compact (avoid the default "documentation" suffix).
html_title = f"{project} {release}"
html_short_title = html_title

# --- Extensions ---
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.todo",
    "sphinx.ext.viewcode",
    "myst_parser",
    "sphinx.ext.mathjax",
    "nbsphinx",
    "sphinxcontrib.youtube",
]

mathjax3_config = {
    "tex": {
        "inlineMath": [["$", "$"], ["\\(", "\\)"]],
        "displayMath": [["$$", "$$"], ["\\[", "\\]"]],
    }
}

# Follow symlinks in examples/tutorial assets.
followlinks = True
nbsphinx_execute = "never"
nbsphinx_outputdir = "_images"

# Build autosummary pages for modules/classes/functions automatically.
autosummary_generate = True

# Autodoc settings.
autodoc_default_options = {
    "members": True,
    "undoc-members": False,
    "inherited-members": True,
    "show-inheritance": True,
}
autodoc_typehints = "description"
autodoc_class_signature = "separated"
autodoc_preserve_defaults = True

# Mock heavy/optional runtime dependencies to keep docs build robust.
autodoc_mock_imports = [
    "ase",
    "fastapi",
    "httpx",
    "meep",
    "passlib",
    "psi4",
    "qutip",
    "sqlalchemy",
    "uvicorn",
]

# Napoleon (Google/NumPy docstrings).
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_use_param = True
napoleon_use_rtype = True

# Theme.
html_theme = "furo"

html_theme_options = {
    "sidebar_hide_name": False,
    "top_of_page_button": "edit",
    "navigation_with_keys": False,
    "light_logo": "img/mark.svg",
    "dark_logo": "img/mark.svg",
    "light_css_variables": {
        "color-brand-primary": "#1264a3",
        "color-brand-content": "#0d2a4d",
        "color-sidebar-background": "#f6f9ff",
        "color-admonition-background": "rgba(18, 100, 163, 0.08)",
    },
    "dark_css_variables": {
        "color-brand-primary": "#66c7ff",
        "color-brand-content": "#d6ecff",
        "color-sidebar-background": "#0d1829",
        "color-admonition-background": "rgba(102, 199, 255, 0.12)",
    },
}

# General Sphinx settings.
templates_path = ["_templates"]
exclude_patterns = ["_build"]
html_static_path = ["_static"]
html_css_files = [
    "css/custom.css",
]
html_js_files = [
    "js/theme-light-only.js",
]

# Ensure referrer header is sent for embedded content.
html_meta = {
    "referrer": "strict-origin-when-cross-origin",
}


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def _github_repo_url_from_zip_url(zip_url: str) -> str:
    parsed = urlparse(zip_url)
    if parsed.scheme not in {"http", "https"}:
        return zip_url
    if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
        return zip_url
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2:
        return zip_url
    return f"{parsed.scheme}://{parsed.netloc}/{segments[0]}/{segments[1]}"


def _repo_label(repo_url: str) -> str:
    parsed = urlparse(repo_url)
    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) >= 2:
        return f"{segments[0]}/{segments[1]}"
    return repo_url


def _format_updated_at_est(updated_at_raw: object) -> str:
    updated_at = _collapse_whitespace(str(updated_at_raw or "unknown"))
    if updated_at.lower() == "unknown":
        return updated_at
    try:
        normalized = (
            updated_at[:-1] + "+00:00" if updated_at.endswith("Z") else updated_at
        )
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(EST_TZ).strftime("%Y-%m-%d %H:%M:%S EST")
    except ValueError:
        return updated_at


def _build_builtin_packages_page(payload: dict[str, object]) -> str:
    updated_at = _format_updated_at_est(payload.get("updated_at"))
    raw_packages = payload.get("packages")
    if not isinstance(raw_packages, list):
        raise ValueError("Curated channel payload must contain a list at 'packages'.")

    rows: list[tuple[str, str, str, str]] = []
    for index, item in enumerate(raw_packages):
        if not isinstance(item, dict):
            raise ValueError(f"Curated package at index {index} must be an object.")
        package_id = _collapse_whitespace(str(item.get("package_id") or ""))
        title = _collapse_whitespace(str(item.get("title") or ""))
        zip_url = _collapse_whitespace(str(item.get("zip_url") or ""))
        if not package_id or not title or not zip_url:
            raise ValueError(
                f"Curated package at index {index} missing package_id/title/zip_url."
            )
        repo_url = _github_repo_url_from_zip_url(zip_url)
        rows.append((package_id, title, _repo_label(repo_url), repo_url))

    rows.sort(key=lambda row: row[0].lower())
    lines = [
        ":orphan:",
        "",
        "Built-in Supported Scientific Packages",
        "======================================",
        "",
        f"- Last curated update: ``{updated_at}``",
        f"- Total built-in packages: ``{len(rows)}``",
        "",
        ".. list-table:: Built-in package catalog",
        "   :header-rows: 1",
        "   :widths: 18 52 30",
        "",
        "   * - Package ID",
        "     - Title",
        "     - Repository",
    ]
    for package_id, title, repo_label, repo_url in rows:
        lines.extend(
            [
                f"   * - ``{package_id}``",
                f"     - {title}",
                f"     - `{repo_label} <{repo_url}>`_",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def _generate_built_in_scientific_packages_page(
    channel_path: Path = CURATED_SCIPKG_CHANNEL,
    output_path: Path = BUILTIN_SCIPKG_PAGE,
) -> None:
    payload = json.loads(channel_path.read_text(encoding="utf-8"))
    rendered = _build_builtin_packages_page(payload)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")


def _on_builder_inited(app: object) -> None:
    del app
    _generate_built_in_scientific_packages_page()


def setup(app: object) -> dict[str, bool]:
    app.connect("builder-inited", _on_builder_inited)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
