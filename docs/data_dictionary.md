# Data dictionary

Files are written to `data/processed/`. Read `cik` as text to keep its leading zeros. Empty means not reported or not applicable, never zero. `quality_status` is `not_validated` until the Phase 3 rules exist.

## financial_facts.csv: one row per selected core metric
`supporting_facts.csv` has the same columns, for prior-year and supporting fields.

| Column | Type | Meaning |
|---|---|---|
| cik | text | 10-digit SEC company identifier |
| ticker, company_name | text | From the SEC ticker mapping |
| fiscal_year | integer | The company's own fiscal year |
| period_start | date | Empty for year-end metrics |
| period_end | date | Fiscal year end |
| metric | text | Name from `config/metrics.json` |
| value | number | As reported, unscaled |
| unit | text | `USD` or `USD/shares` |
| source_tag, taxonomy | text | XBRL tag and taxonomy |
| form, filed_date, accession_number | text, date, text | The filing the value came from |
| source_url | text | Link to that filing |
| retrieved_at | timestamp | When the SEC response was downloaded (UTC) |
| selection_note | text | See [methodology](methodology.md) |
| quality_status | text | Validation status |

## company_financials.csv: one row per expected company-year

`ticker`, `cik`, `company_name`, `fiscal_year`, `period_end`, the seven metrics, `currency`, `missing_metrics` (names separated by `;`) and `quality_status`.

## candidate_facts.csv: every eligible fact considered

The same columns without `selection_note`, plus `tag_priority` and the SEC's `fy`, `fp` and `frame`.

## run_manifest.json

Run ID, status, cutoff, scope, failed companies, configuration versions, counts, selection notes, conflicts, stage durations, and each source file's URL, retrieval time, size and SHA-256.
