from __future__ import annotations

import json

from fermilink import cli


def test_cli_avail_exact_match(capsys) -> None:
    code = cli.main(["avail", "maxwelllink"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Found" in out
    assert "channel 'skilled-scipkg'" in out
    assert "for 'maxwelllink'" in out
    assert "maxwelllink: MaxwellLink" in out


def test_cli_avail_missing_match(capsys) -> None:
    code = cli.main(["avail", "package-that-does-not-exist"])
    assert code == 0
    out = capsys.readouterr().out
    assert "No curated package matched" in out


def test_cli_avail_json_output(capsys) -> None:
    code = cli.main(["avail", "maxwelllink", "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["channel"] == "skilled-scipkg"
    assert payload["query"] == "maxwelllink"
    assert payload["found"] is True
    results = payload["results"]
    assert isinstance(results, list)
    maxwelllink = next(
        item for item in results if str(item.get("package_id")) == "maxwelllink"
    )
    assert str(maxwelllink.get("default_version")) == "branch-head"
    assert isinstance(maxwelllink.get("versions"), list)
    assert bool(maxwelllink.get("description"))
    assert bool(maxwelllink.get("upstream_repo_url"))
