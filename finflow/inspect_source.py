"""Phase 1 source inspection: can each metric be found for each fiscal year?

This reports what the SEC data contains. It does not choose final values.
"""

import pandas as pd

from finflow.transform import (
    add_fiscal_year_labels,
    flatten_company_facts,
    list_annual_filings,
    match_annual_facts,
)

INSPECTION_COLUMNS = [
    "ticker", "fiscal_year", "period_end", "metric", "core", "tag", "unit", "start",
    "latest_value", "latest_filed", "latest_accn", "filings_reporting", "distinct_values",
    "finding",
]


def classify(per_tag):
    """Describe how the candidate tags for one metric and year relate to each other."""
    if per_tag.empty:
        return "missing"
    if per_tag["latest_value"].nunique() > 1:
        return "tags_disagree"
    if (per_tag["distinct_values"] > 1).any():
        return "revised_over_time"
    return "one_tag" if len(per_tag) == 1 else "tags_agree"


def summarise_tags(matched):
    """Return one row per tag holding its most recently filed value."""
    rows = []
    for tag, group in matched.sort_values(["filed", "accn"]).groupby("tag", sort=False):
        latest = group.iloc[-1]
        rows.append(
            {
                "tag": tag,
                "unit": latest["unit"],
                "start": latest["start"],
                "latest_value": latest["value"],
                "latest_filed": latest["filed"],
                "latest_accn": latest["accn"],
                "filings_reporting": group["accn"].nunique(),
                "distinct_values": group["value"].nunique(),
            }
        )
    return pd.DataFrame(rows)


def inspect_company(ticker, cik, company_data, metrics, start_year, end_year, as_of):
    """Return (filings, findings) tables for one company."""
    facts = flatten_company_facts(company_data["facts"], metrics)
    filings = list_annual_filings(company_data["filing_tables"], cik, as_of)
    filings = add_fiscal_year_labels(filings, facts)
    targets = filings[
        (filings["form"] == "10-K") & filings["fiscal_year"].between(start_year, end_year)
    ]

    rows = []
    for target in targets.itertuples():
        matched = match_annual_facts(facts, target.period_end, as_of)
        for metric in metrics:
            per_tag = summarise_tags(matched[matched["metric"] == metric["name"]])
            base = {
                "ticker": ticker,
                "fiscal_year": target.fiscal_year,
                "period_end": target.period_end,
                "metric": metric["name"],
                "core": metric["core"],
                "finding": classify(per_tag),
            }
            if per_tag.empty:
                rows.append(base)
            else:
                rows.extend({**base, **row} for row in per_tag.to_dict("records"))
    return filings, pd.DataFrame(rows, columns=INSPECTION_COLUMNS)
