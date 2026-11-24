"""Transform stage: turn nested SEC JSON into flat tables.

Phase 1 covers flattening and period matching only. Choosing one canonical value per
company, period and metric is added in Phase 2.
"""

import pandas as pd

ANNUAL_FORMS = ["10-K", "10-K/A"]
# 52-week years last 364 days and 53-week years 371. The window excludes quarters.
MIN_ANNUAL_DAYS = 350
MAX_ANNUAL_DAYS = 380

FACT_COLUMNS = [
    "metric", "core", "tag_priority", "expected_unit", "period_type",
    "taxonomy", "tag", "unit", "value", "start", "end",
    "fy", "fp", "form", "filed", "accn", "frame",
]


def flatten_company_facts(facts_json, metrics):
    """Return one row per reported fact for every configured tag, keeping all source fields."""
    us_gaap = facts_json.get("facts", {}).get("us-gaap", {})
    rows = []
    for metric in metrics:
        for priority, tag in enumerate(metric["tags"], start=1):
            for unit, facts in us_gaap.get(tag, {}).get("units", {}).items():
                for fact in facts:
                    rows.append(
                        {
                            "metric": metric["name"],
                            "core": metric["core"],
                            "tag_priority": priority,
                            "expected_unit": metric["unit"],
                            "period_type": metric["period_type"],
                            "taxonomy": "us-gaap",
                            "tag": tag,
                            "unit": unit,
                            "value": fact.get("val"),
                            "start": fact.get("start"),
                            "end": fact.get("end"),
                            "fy": fact.get("fy"),
                            "fp": fact.get("fp"),
                            "form": fact.get("form"),
                            "filed": fact.get("filed"),
                            "accn": fact.get("accn"),
                            "frame": fact.get("frame"),
                        }
                    )
    table = pd.DataFrame(rows, columns=FACT_COLUMNS)
    for column in ["start", "end", "filed"]:
        table[column] = pd.to_datetime(table[column])
    return table


def list_annual_filings(filing_tables, cik, as_of):
    """Return 10-K and 10-K/A filings submitted on or before the as-of date."""
    columns = ["accessionNumber", "form", "filingDate", "reportDate", "primaryDocument"]
    frames = [pd.DataFrame({c: table[c] for c in columns}) for table in filing_tables]
    filings = pd.concat(frames, ignore_index=True).drop_duplicates("accessionNumber")
    filings = filings[filings["form"].isin(ANNUAL_FORMS)].copy()
    filings["filed"] = pd.to_datetime(filings["filingDate"])
    filings["period_end"] = pd.to_datetime(filings["reportDate"])
    filings = filings[filings["filed"] <= pd.Timestamp(as_of)]
    filings = filings.rename(columns={"accessionNumber": "accn"})
    filings["source_url"] = [
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{accn.replace('-', '')}/{document}"
        for accn, document in zip(filings["accn"], filings["primaryDocument"])
    ]
    columns = ["accn", "form", "filed", "period_end", "source_url"]
    return filings[columns].sort_values(["period_end", "filed"]).reset_index(drop=True)


def add_fiscal_year_labels(filings, facts):
    """Label each filing with the fiscal year reported in its own facts.

    A fact's fy describes the filing it came from, not the year being measured, so it
    is used here to label filings and never to select observations. If a filing's facts
    disagree, the label is left missing for review.
    """
    labels = {}
    for accn, group in facts.dropna(subset=["fy"]).groupby("accn"):
        years = group["fy"].unique()
        labels[accn] = int(years[0]) if len(years) == 1 else None
    labelled = filings.copy()
    labelled["fiscal_year"] = labelled["accn"].map(labels).astype("Int64")
    return labelled


def match_annual_facts(facts, period_end, as_of):
    """Return facts for one fiscal year end, matched on dates rather than on fy or fp."""
    eligible = facts[
        facts["form"].isin(ANNUAL_FORMS)
        & (facts["filed"] <= pd.Timestamp(as_of))
        & (facts["unit"] == facts["expected_unit"])
        & (facts["end"] == pd.Timestamp(period_end))
    ]
    days = (eligible["end"] - eligible["start"]).dt.days + 1
    is_annual = (eligible["period_type"] == "duration") & days.between(
        MIN_ANNUAL_DAYS, MAX_ANNUAL_DAYS
    )
    is_instant = (eligible["period_type"] == "instant") & eligible["start"].isna()
    return eligible[is_annual | is_instant].copy()
