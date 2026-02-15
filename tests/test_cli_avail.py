from __future__ import annotations

import json

from fermilink import cli


def test_cli_avail_exact_match(capsys) -> None:
    code = cli.main(["avail", "ase"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Found" in out
    assert "ase: ASE" in out
    assert "skilled-scipkg/ase" in out


def test_cli_avail_missing_match(capsys) -> None:
    code = cli.main(["avail", "package-that-does-not-exist"])
    assert code == 0
    out = capsys.readouterr().out
    assert "No curated package matched" in out


def test_cli_avail_json_output(capsys) -> None:
    code = cli.main(["avail", "ase", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["channel"] == "skilled-scipkg"
    assert payload["query"] == "ase"
    assert payload["found"] is True
    results = payload["results"]
    assert isinstance(results, list)
    ase = next(item for item in results if str(item.get("package_id")) == "ase")
    assert str(ase.get("default_version")) == "branch-head"
    assert isinstance(ase.get("versions"), list)
    assert bool(ase.get("description"))
    assert bool(ase.get("upstream_repo_url"))
