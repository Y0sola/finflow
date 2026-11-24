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


class TransformError(Exception):
    """Source data does not have the structure the selection rules require."""


def resolve_fiscal_years(filings, start_year, end_year):
    """Return one original 10-K per fiscal year, defining that year's period end."""
    targets = filings[
        (filings["form"] == "10-K") & filings["fiscal_year"].between(start_year, end_year)
    ]
    repeated = targets.loc[targets["fiscal_year"].duplicated(), "fiscal_year"]
    if not repeated.empty:
        raise TransformError(f"More than one 10-K is labelled fiscal {sorted(set(repeated))}")
    return targets[["fiscal_year", "period_end"]].reset_index(drop=True)


def collect_candidates(facts, fiscal_years, as_of):
    """Return every eligible fact for the target fiscal years, labelled with its year."""
    frames = []
    for target in fiscal_years.itertuples():
        matched = match_annual_facts(facts, target.period_end, as_of)
        frames.append(matched.assign(fiscal_year=target.fiscal_year))
    if not frames:
        return facts.iloc[0:0].assign(fiscal_year=pd.Series(dtype="int64"))
    return pd.concat(frames, ignore_index=True)


def select_one(candidates):
    """Choose one fact from the candidates for a single company, year and metric.

    1. Use the highest-priority tag that was reported.
    2. Within that tag, use the most recently filed value (latest-known convention).
    3. If that filing date carries different values, choose none and flag a conflict.

    Returns (row, note). row is None when the conflict cannot be resolved.
    """
    best_tag = candidates[candidates["tag_priority"] == candidates["tag_priority"].min()]
    latest = best_tag[best_tag["filed"] == best_tag["filed"].max()]
    if latest["value"].nunique(dropna=False) > 1:
        return None, "conflicting_values_same_date"

    # Equal values filed on the same day: the highest accession number, so reruns agree.
    chosen = latest.sort_values(["accn", "start"]).iloc[-1]
    notes = []
    if best_tag["value"].nunique() > 1:
        notes.append("restated")
    others = candidates[
        (candidates["tag_priority"] > chosen["tag_priority"])
        & (candidates["value"] != chosen["value"])
    ]
    if (others["filed"] > chosen["filed"]).any():
        notes.append("review_other_tag_filed_later")
    elif not others.empty:
        notes.append("other_tag_differs")
    return chosen, ";".join(notes)


def select_canonical_facts(candidates):
    """Return (selected facts, unresolved conflicts), at most one row per year and metric."""
    selected, conflicts = [], []
    for (fiscal_year, metric), group in candidates.groupby(["fiscal_year", "metric"]):
        chosen, note = select_one(group)
        if chosen is None:
            conflicts.append(
                {"fiscal_year": fiscal_year, "metric": metric, "note": note,
                 "values": sorted(group["value"].unique().tolist())}
            )
        else:
            selected.append({**chosen.to_dict(), "selection_note": note})
    columns = [*candidates.columns, "selection_note"]
    return pd.DataFrame(selected, columns=columns), conflicts


def build_wide_table(facts, companies, fiscal_years, metric_names):
    """Pivot selected facts to one row per expected company-year, keeping gaps empty.

    Starting from the full grid of expected rows means a company-year with no data
    still appears instead of silently vanishing.
    """
    grid = pd.DataFrame(companies)[["ticker", "cik", "company_name"]].merge(
        pd.DataFrame({"fiscal_year": fiscal_years}), how="cross"
    )
    keys = ["ticker", "fiscal_year"]
    if facts.duplicated([*keys, "metric"]).any():
        raise TransformError("Selected facts contain duplicate company, year and metric keys")
    values = facts.pivot(index=keys, columns="metric", values="value")
    values = values.reindex(columns=metric_names).reset_index()
    period_ends = facts[[*keys, "period_end"]].drop_duplicates()
    wide = grid.merge(period_ends, on=keys, how="left", validate="one_to_one")
    wide = wide.merge(values, on=keys, how="left", validate="one_to_one")
    has_usd = facts[facts["unit"] == "USD"].groupby(keys).size()
    wide["currency"] = ["USD" if (t, y) in has_usd.index else None
                        for t, y in zip(wide["ticker"], wide["fiscal_year"])]
    wide["missing_metrics"] = [
        ";".join(name for name in metric_names if pd.isna(row[name]))
        for _, row in wide.iterrows()
    ]
    wide["quality_status"] = "not_validated"
    return wide
