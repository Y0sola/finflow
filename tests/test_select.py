import pandas as pd
import pytest

from finflow.transform import (
    TransformError,
    build_wide_table,
    collect_candidates,
    flatten_company_facts,
    resolve_fiscal_years,
    select_canonical_facts,
    select_one,
)

YEARS = pd.DataFrame(
    {"fiscal_year": [2022, 2023], "period_end": pd.to_datetime(["2022-12-31", "2024-01-06"])}
)


def select(load_fixture, metrics, as_of="2024-12-31"):
    facts = flatten_company_facts(load_fixture("companyfacts_synthetic.json"), metrics)
    selected, conflicts = select_canonical_facts(collect_candidates(facts, YEARS, as_of))
    return selected.set_index(["fiscal_year", "metric"]), conflicts


def candidate(value, filed, tag="A", priority=1, accn="1"):
    return {"tag": tag, "tag_priority": priority, "value": value,
            "filed": pd.Timestamp(filed), "accn": accn, "start": pd.Timestamp("2023-01-01")}


def test_repeated_comparatives_become_one_row_from_the_latest_filing(load_fixture, metrics):
    selected, conflicts = select(load_fixture, metrics)
    assert selected.index.is_unique and conflicts == []
    revenue = selected.loc[(2022, "revenue")]
    assert revenue["value"] == 1000 and revenue["accn"] == "0000000001-24-000001"
    assert revenue["selection_note"] == ""


def test_amendment_replaces_only_the_metric_it_reports(load_fixture, metrics):
    selected, _ = select(load_fixture, metrics)
    income = selected.loc[(2023, "net_income")]
    assert (income["value"], income["form"], income["selection_note"]) == (95, "10-K/A", "restated")
    assert selected.loc[(2023, "revenue"), "form"] == "10-K"


def test_cutoff_before_the_amendment_gives_the_original_value(load_fixture, metrics):
    selected, _ = select(load_fixture, metrics, as_of="2024-05-31")
    assert selected.loc[(2023, "net_income"), "value"] == 100


def test_priority_tag_wins_and_the_difference_is_noted(load_fixture, metrics):
    selected, _ = select(load_fixture, metrics)
    revenue = selected.loc[(2023, "revenue")]
    assert (revenue["tag"], revenue["value"]) == ("Revenues", 1200)
    assert revenue["selection_note"] == "other_tag_differs"


def test_missing_metric_is_absent_and_zero_is_kept(load_fixture, metrics):
    selected, _ = select(load_fixture, metrics)
    assert (2023, "total_liabilities") not in selected.index
    assert selected.loc[(2023, "operating_cash_flow"), "value"] == 0


def test_different_values_on_the_same_date_are_not_resolved():
    rows = pd.DataFrame([candidate(1, "2024-03-01", accn="1"), candidate(2, "2024-03-01", accn="2")])
    assert select_one(rows) == (None, "conflicting_values_same_date")
    rows["fiscal_year"], rows["metric"] = 2023, "revenue"
    selected, conflicts = select_canonical_facts(rows)
    assert selected.empty
    assert conflicts[0]["values"] == [1, 2]


def test_lower_priority_tag_filed_later_is_flagged_for_review():
    rows = pd.DataFrame([candidate(1, "2024-03-01"), candidate(2, "2025-03-01", "B", 2)])
    chosen, note = select_one(rows)
    assert (chosen["value"], note) == (1, "review_other_tag_filed_later")


def test_equal_values_on_the_same_date_are_chosen_in_a_fixed_order():
    rows = pd.DataFrame([candidate(1, "2024-03-01", accn="2"), candidate(1, "2024-03-01", accn="1")])
    for order in (rows, rows.iloc[::-1]):
        assert select_one(order)[0]["accn"] == "2"


def test_two_filings_claiming_one_fiscal_year_are_rejected():
    filings = pd.DataFrame(
        {"form": ["10-K", "10-K"], "fiscal_year": [2023, 2023],
         "period_end": pd.to_datetime(["2023-12-31", "2024-12-31"])}
    )
    with pytest.raises(TransformError, match="2023"):
        resolve_fiscal_years(filings, 2022, 2025)


def wide_input(rows):
    return pd.DataFrame(rows, columns=["ticker", "fiscal_year", "period_end", "metric", "value", "unit"])


COMPANIES = [{"ticker": "SYN", "cik": "0000000001", "company_name": "Synthetic Co"}]


def test_wide_table_keeps_every_expected_row_with_empty_gaps():
    facts = wide_input([("SYN", 2023, "2024-01-06", "revenue", 1200, "USD")])
    wide = build_wide_table(facts, COMPANIES, [2023, 2024], ["revenue", "net_income"])
    assert wide["fiscal_year"].tolist() == [2023, 2024]
    assert wide["net_income"].isna().all()
    assert wide["missing_metrics"].tolist() == ["net_income", "revenue;net_income"]


def test_wide_table_rejects_duplicate_keys():
    row = ("SYN", 2023, "2024-01-06", "revenue", 1200, "USD")
    with pytest.raises(TransformError, match="duplicate"):
        build_wide_table(wide_input([row, row]), COMPANIES, [2023], ["revenue"])
