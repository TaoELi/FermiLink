from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path

# --- Make the package importable ---
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

# --- Project info ---
project = "FermiLink"
author = "FermiLink"
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
    "light_logo": "img/icon.png",
    "dark_logo": "img/icon.png",
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
