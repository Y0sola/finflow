import json

import pandas as pd
from conftest import URLS

from finflow.__main__ import main

def run(client, tmp_path, *extra):
    return main(
        ["inspect", "--tickers", "SYN", *extra, "--start-year", "2023", "--end-year", "2023",
         "--as-of", "2024-12-31", "--raw-dir", str(tmp_path / "raw"),
         "--output-dir", str(tmp_path / "out")],
        client,
    )


def test_run_fetches_only_needed_pages_and_repeats_identically(client, tmp_path, capsys):
    assert run(client, tmp_path) == 0
    assert [url for url, _ in client.session.calls] == list(URLS)  # page 002 is too old
    first = (tmp_path / "out" / "source_inspection.csv").read_bytes()
    assert "Core slots found: 6 of 7 expected" in capsys.readouterr().out

    assert run(client, tmp_path) == 0
    assert len(client.session.calls) == len(URLS)  # second run is fully cached
    assert (tmp_path / "out" / "source_inspection.csv").read_bytes() == first
    assert len(json.loads((tmp_path / "raw" / "manifest.json").read_text())) == len(URLS)


def test_partial_failure_is_reported_and_exit_code_is_1(client, tmp_path, capsys):
    assert run(client, tmp_path, "MISSING") == 1
    output = capsys.readouterr().out
    assert "INCOMPLETE: 1 of 2 companies failed: MISSING" in output
    assert "6 of 14 expected" in output
    assert set(pd.read_csv(tmp_path / "out" / "source_inspection.csv")["ticker"]) == {"SYN"}


def test_missing_contact_exits_with_2_and_makes_no_request(monkeypatch, tmp_path):
    monkeypatch.setenv("SEC_USER_AGENT", "")
    assert run(None, tmp_path) == 2
    assert not (tmp_path / "raw").exists()
