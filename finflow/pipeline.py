"""Run the pipeline stages in order and publish the results safely.

Phase 2 covers extract, transform and export. Validation and ratios are added in Phase 3.
"""

import json
import logging
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from finflow.extract import (
    TICKER_URL,
    SourceError,
    fetch_company,
    fetch_json,
    read_manifest,
    resolve_companies,
)
from finflow.transform import (
    TransformError,
    add_fiscal_year_labels,
    build_wide_table,
    collect_candidates,
    flatten_company_facts,
    list_annual_filings,
    resolve_fiscal_years,
    select_canonical_facts,
)

logger = logging.getLogger(__name__)

FACT_OUTPUT_COLUMNS = [
    "cik", "ticker", "company_name", "fiscal_year", "period_start", "period_end", "metric",
    "value", "unit", "source_tag", "taxonomy", "form", "filed_date", "accession_number",
    "source_url", "retrieved_at", "selection_note", "quality_status",
]
SORT_KEYS = ["ticker", "fiscal_year", "metric"]
OUTPUT_FILES = [
    "financial_facts.csv", "company_financials.csv", "supporting_facts.csv",
    "candidate_facts.csv", "run_manifest.json",
]
RENAMES = {
    "start": "period_start", "end": "period_end", "tag": "source_tag",
    "filed": "filed_date", "accn": "accession_number",
}


def transform_company(company, data, metrics, start_year, end_year, as_of, retrieved_at):
    """Return (selected facts, all candidates, conflicts) for one company.

    The year before start_year is included because growth and average equity need it.
    """
    facts = flatten_company_facts(data["facts"], metrics)
    filings = list_annual_filings(data["filing_tables"], company["cik"], as_of)
    filings = add_fiscal_year_labels(filings, facts)
    fiscal_years = resolve_fiscal_years(filings, start_year - 1, end_year)
    candidates = collect_candidates(facts, fiscal_years, as_of)
    selected, conflicts = select_canonical_facts(candidates)

    urls = filings.set_index("accn")["source_url"]
    for table in (selected, candidates):
        table.insert(0, "cik", company["cik"])
        table.insert(1, "ticker", company["ticker"])
        table.insert(2, "company_name", company["name"])
        table["source_url"] = table["accn"].map(urls)
        table["retrieved_at"] = retrieved_at
        table["quality_status"] = "not_validated"
    for conflict in conflicts:
        conflict["ticker"] = company["ticker"]
    return selected.rename(columns=RENAMES), candidates.rename(columns=RENAMES), conflicts


def run_pipeline(client, tickers, metrics, config_versions, start_year, end_year, as_of,
                 raw_dir, output_dir, refresh=False):
    """Run extract, transform and export. Returns the run manifest as a dictionary."""
    raw_dir, output_dir = Path(raw_dir), Path(output_dir)
    started = datetime.now(timezone.utc)
    run_id = started.strftime("%Y%m%dT%H%M%SZ")
    core_names = [metric["name"] for metric in metrics if metric["core"]]
    seconds = {"extract": 0.0, "transform": 0.0}

    clock = time.perf_counter()
    ticker_data = fetch_json(client, TICKER_URL, "company_tickers.json", raw_dir, refresh)
    seconds["extract"] += time.perf_counter() - clock

    companies, selected, candidates, conflicts, failed = [], [], [], [], {}
    for ticker in tickers:
        try:
            clock = time.perf_counter()
            company = resolve_companies(ticker_data, [ticker])[0]
            data = fetch_company(
                client, company["cik"], raw_dir, f"{start_year - 1}-01-01", refresh
            )
            retrieved_at = read_manifest(raw_dir)[
                f"companyfacts_CIK{company['cik']}.json"]["retrieved_at"]
            seconds["extract"] += time.perf_counter() - clock

            clock = time.perf_counter()
            company_selected, company_candidates, company_conflicts = transform_company(
                company, data, metrics, start_year, end_year, as_of, retrieved_at
            )
            seconds["transform"] += time.perf_counter() - clock
        except (SourceError, TransformError) as error:
            logger.error("%s failed: %s", ticker, error)
            failed[ticker] = str(error)
            continue
        companies.append({**company, "company_name": company["name"]})
        selected.append(company_selected)
        candidates.append(company_candidates)
        conflicts.extend(company_conflicts)

    if not companies:
        raise SourceError("Every company failed, so there is nothing to export")

    clock = time.perf_counter()
    selected = pd.concat(selected, ignore_index=True).sort_values(SORT_KEYS)
    candidates = pd.concat(candidates, ignore_index=True).sort_values(
        [*SORT_KEYS, "tag_priority", "filed_date", "accession_number"]
    )
    in_scope = selected["metric"].isin(core_names) & (selected["fiscal_year"] >= start_year)
    facts = selected[in_scope]
    wide = build_wide_table(facts, companies, range(start_year, end_year + 1), core_names)
    seconds["transform"] += time.perf_counter() - clock

    expected = len(tickers) * (end_year - start_year + 1) * len(core_names)
    core_conflicts = [c for c in conflicts
                      if c["metric"] in core_names and c["fiscal_year"] >= start_year]
    manifest = {
        "run_id": run_id,
        "status": "incomplete" if failed else "complete",
        "stages": ["extract", "transform", "export"],
        "as_of": as_of,
        "fiscal_years": [start_year, end_year],
        "tickers": list(tickers),
        "failed_companies": failed,
        "config_versions": config_versions,
        "counts": {
            "expected_core_slots": expected,
            "selected_core_facts": len(facts),
            "unresolved_core_conflicts": len(core_conflicts),
            "missing_core_slots": expected - len(facts) - len(core_conflicts),
            "company_year_rows": len(wide),
            "supporting_facts": int((~in_scope).sum()),
            "candidate_facts": len(candidates),
        },
        "selection_notes": facts["selection_note"].replace("", "none").value_counts().to_dict(),
        "unresolved_conflicts": conflicts,
        "stage_seconds": {name: round(value, 3) for name, value in seconds.items()},
        "sources": read_manifest(raw_dir),
    }
    publish(
        output_dir,
        {
            "financial_facts.csv": facts[FACT_OUTPUT_COLUMNS],
            "company_financials.csv": wide,
            "supporting_facts.csv": selected[~in_scope][FACT_OUTPUT_COLUMNS],
            "candidate_facts.csv": candidates[
                [c for c in FACT_OUTPUT_COLUMNS if c != "selection_note"]
                + ["tag_priority", "fy", "fp", "frame"]
            ],
        },
        manifest,
    )
    return manifest


def publish(output_dir, tables, manifest):
    """Write everything to a staging folder, then replace the published files.

    An incomplete run goes to its own folder so it never replaces a good dataset.
    """
    staging = output_dir / f"staging-{manifest['run_id']}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for name, table in tables.items():
        # 15 significant digits keeps whole dollars exact and avoids a trailing ".0".
        table.to_csv(staging / name, index=False, float_format="%.15g", date_format="%Y-%m-%d")
    (staging / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    destination = output_dir if manifest["status"] == "complete" else output_dir / "incomplete"
    destination.mkdir(parents=True, exist_ok=True)
    for name in OUTPUT_FILES:
        os.replace(staging / name, destination / name)
    staging.rmdir()
    logger.info("published %s run to %s", manifest["status"], destination)
