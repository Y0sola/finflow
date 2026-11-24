import pandas as pd
import pytest

from finflow.inspect_source import inspect_company
from finflow.transform import (
    add_fiscal_year_labels,
    flatten_company_facts,
    list_annual_filings,
    match_annual_facts,
)

AS_OF = "2024-12-31"
END_52_WEEK = "2022-12-31"
END_53_WEEK = "2024-01-06"


@pytest.fixture
def facts(load_fixture, metrics):
    return flatten_company_facts(load_fixture("companyfacts_synthetic.json"), metrics)


@pytest.fixture
def filing_tables(load_fixture):
    return [
        load_fixture("submissions_synthetic.json")["filings"]["recent"],
        load_fixture("submissions_page_synthetic.json"),
    ]


def values(matched, tag):
    return matched.loc[matched["tag"] == tag, "value"].tolist()


def test_flatten_keeps_every_candidate_and_its_source_fields(facts):
    assert len(facts) == 18
    assert facts["accn"].notna().all()
    assert len(facts[facts["tag"] == "Revenues"]) == 5


def test_flatten_unknown_tags_gives_an_empty_table_with_columns(metrics):
    table = flatten_company_facts({"facts": {}}, metrics)
    assert table.empty and "accn" in table.columns


def test_quarter_and_nine_month_values_are_excluded(facts):
    matched = match_annual_facts(facts, END_53_WEEK, AS_OF)
    assert values(matched, "Revenues") == [1200]


def test_52_and_53_week_years_both_match(facts):
    for end, days in [(END_52_WEEK, 364), (END_53_WEEK, 371)]:
        matched = match_annual_facts(facts, end, AS_OF)
        row = matched[matched["tag"] == "Revenues"].iloc[0]
        assert (row["end"] - row["start"]).days + 1 == days


def test_comparative_value_matches_its_own_period_despite_fy_label(facts):
    matched = match_annual_facts(facts, END_52_WEEK, AS_OF)
    revenue = matched[matched["tag"] == "Revenues"]
    assert revenue["fy"].tolist() == [2022, 2023]  # fy describes the filing, not the period
    assert revenue["value"].tolist() == [1000, 1000]


def test_wrong_unit_is_excluded(facts):
    matched = match_annual_facts(facts, END_53_WEEK, AS_OF)
    assert values(matched, "EarningsPerShareDiluted") == [1.25]


def test_zero_and_negative_values_are_kept(facts):
    assert values(match_annual_facts(facts, END_52_WEEK, AS_OF), "NetIncomeLoss") == [-50]
    matched = match_annual_facts(facts, END_53_WEEK, AS_OF)
    assert values(matched, "NetCashProvidedByUsedInOperatingActivities") == [0]


def test_facts_filed_after_the_cutoff_are_excluded(facts):
    before_amendment = match_annual_facts(facts, END_53_WEEK, "2024-05-31")
    assert values(before_amendment, "NetIncomeLoss") == [100]
    after = match_annual_facts(facts, END_53_WEEK, "2024-06-01")
    assert values(after, "NetIncomeLoss") == [100, 95]


def test_unsupported_form_is_excluded(facts):
    changed = facts.copy()
    changed["form"] = "8-K"
    assert match_annual_facts(changed, END_53_WEEK, AS_OF).empty


def test_annual_filings_come_from_all_pages_with_links(filing_tables):
    filings = list_annual_filings(filing_tables, "0000000001", AS_OF)
    assert filings["form"].tolist() == ["10-K", "10-K", "10-K", "10-K/A"]
    assert filings["source_url"].iloc[1] == (
        "https://www.sec.gov/Archives/edgar/data/1/000000000123000001/k23.htm"
    )
    assert len(list_annual_filings(filing_tables, "0000000001", "2023-12-31")) == 2


def test_fiscal_year_label_is_missing_when_facts_disagree(facts, filing_tables):
    filings = list_annual_filings(filing_tables, "0000000001", AS_OF)
    labels = add_fiscal_year_labels(filings, facts)["fiscal_year"]
    assert labels.iloc[1:].tolist() == [2022, 2023, 2023]
    assert pd.isna(labels.iloc[0])  # no facts for this filing
    changed = facts.copy()
    changed.loc[changed["accn"] == "0000000001-23-000001", "fy"] = [2022] * 4 + [2021]
    assert pd.isna(add_fiscal_year_labels(filings, changed)["fiscal_year"].iloc[1])


def test_inspection_findings(load_fixture, filing_tables, metrics):
    data = {"facts": load_fixture("companyfacts_synthetic.json"), "filing_tables": filing_tables}
    _, findings = inspect_company("SYN", "0000000001", data, metrics, 2022, 2023, AS_OF)
    found = findings.drop_duplicates(["fiscal_year", "metric"]).set_index(
        ["fiscal_year", "metric"]
    )["finding"]
    assert found[(2022, "revenue")] == "tags_agree"
    assert found[(2023, "revenue")] == "tags_disagree"
    assert found[(2023, "net_income")] == "revised_over_time"
    assert found[(2023, "total_assets")] == "one_tag"
    assert found[(2023, "total_liabilities")] == "missing"
    missing = findings[findings["finding"] == "missing"]
    assert missing["latest_value"].isna().all()  # missing stays missing, never zero


def test_revenue_prefers_total_over_contract_revenue(metrics):
    # Walmart reports both tags; only Revenues includes membership and other income.
    revenue = next(metric for metric in metrics if metric["name"] == "revenue")
    assert revenue["tags"][0] == "Revenues"
    core = [metric["name"] for metric in metrics if metric["core"]]
    assert len(core) == 7
