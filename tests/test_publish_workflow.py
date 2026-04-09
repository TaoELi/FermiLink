from __future__ import annotations

import re
import textwrap
from pathlib import Path


def _publish_workflow_path() -> Path:
    return Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish.yml"


def test_publish_workflow_verifies_wheel_with_declared_dependencies() -> None:
    workflow = _publish_workflow_path().read_text(encoding="utf-8")
    match = re.search(
        r"- name: Verify fermilink CLI from built wheel\n"
        r"(?:\s+.+\n)*?"
        r"\s+run: \|\n"
        r"(?P<body>(?:\s{10}.+\n)+)",
        workflow,
    )
    assert match is not None

    body = textwrap.dedent(match.group("body"))
    assert "python -m pip install --force-reinstall dist/*.whl" in body
    assert "--no-deps" not in body
    assert "python -m pip check" in body
    assert "fermilink --help" in body
