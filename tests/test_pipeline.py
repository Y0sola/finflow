import json

import pandas as pd
import pytest
from conftest import URLS, FakeResponse

from finflow.__main__ import main

CSV_FILES = ["financial_facts.csv", "company_financials.csv", "supporting_facts.csv",
             "candidate_facts.csv"]


def run(client, tmp_path, *extra):
    return main(
        ["run", "--tickers", "SYN", *extra, "--start-year", "2023", "--end-year", "2023",
         "--as-of", "2024-12-31", "--raw-dir", str(tmp_path / "raw"),
         "--output-dir", str(tmp_path / "out")],
        client,
    )


def read(tmp_path, name):
    return pd.read_csv(tmp_path / "out" / name, dtype={"cik": str})


def test_end_to_end_exports(client, tmp_path):
    assert run(client, tmp_path) == 0
    facts = read(tmp_path, "financial_facts.csv")
    assert len(facts) == 6 and set(facts["cik"]) == {"0000000001"}
    assert dict(zip(facts["metric"], facts["value"])) == {
        "revenue": 1200, "net_income": 95, "total_assets": 5500, "shareholders_equity": 2300,
        "operating_cash_flow": 0, "diluted_eps": 1.25,
    }
    lineage = ["source_tag", "accession_number", "filed_date", "source_url", "retrieved_at"]
    assert facts[lineage].notna().all().all()
    instants = facts[facts["metric"] == "total_assets"]
    assert instants["period_start"].isna().all()

    wide = read(tmp_path, "company_financials.csv")
    assert len(wide) == 1 and wide["missing_metrics"].tolist() == ["total_liabilities"]
    assert wide["total_liabilities"].isna().all()
    assert set(read(tmp_path, "supporting_facts.csv")["fiscal_year"]) == {2022}

    manifest = json.loads((tmp_path / "out" / "run_manifest.json").read_text())
    assert manifest["status"] == "complete" and len(manifest["sources"]) == len(URLS)
    counts = manifest["counts"]
    assert counts["expected_core_slots"] == 7
    assert counts["selected_core_facts"] == 6 and counts["missing_core_slots"] == 1
    assert counts["candidate_facts"] == len(read(tmp_path, "candidate_facts.csv"))


def test_rerun_is_identical_and_adds_no_rows(client, tmp_path):
    run(client, tmp_path)
    first = {name: (tmp_path / "out" / name).read_bytes() for name in CSV_FILES}
    run(client, tmp_path)
    assert first == {name: (tmp_path / "out" / name).read_bytes() for name in CSV_FILES}


def test_incomplete_run_never_replaces_a_good_dataset(client, tmp_path):
    run(client, tmp_path)
    good = {p.name: p.read_bytes() for p in (tmp_path / "out").iterdir()}
    assert run(client, tmp_path, "MISSING") == 1
    assert {n: (tmp_path / "out" / n).read_bytes() for n in good} == good
    manifest = json.loads((tmp_path / "out" / "incomplete" / "run_manifest.json").read_text())
    assert manifest["status"] == "incomplete" and list(manifest["failed_companies"]) == ["MISSING"]
    assert manifest["counts"]["expected_core_slots"] == 14
    assert not list((tmp_path / "out").glob("staging-*"))


def test_run_with_no_successful_company_exits_with_2(client, tmp_path):
    for url in list(URLS)[1:]:
        client.session.responses[url] = FakeResponse(403)
    assert run(client, tmp_path) == 2
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("name", CSV_FILES)
def test_whole_dollars_are_written_exactly(client, tmp_path, name):
    run(client, tmp_path)
    text = (tmp_path / "out" / name).read_text()
    assert ".0," not in text and "e+" not in text
