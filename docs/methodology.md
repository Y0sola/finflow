# Selection methodology

How FinFlow chooses one value per company, fiscal year and metric (implemented in `finflow/transform.py`).

1. **Fiscal years.** Each company's original 10-K defines its period end. Two 10-Ks labelled with the same year stop that company with an error.
2. **Eligible candidates.** 10-K and 10-K/A facts filed by the cutoff, in the expected unit, with matching dates. Full-year metrics must cover 350 to 380 days; year-end metrics must have no start date.
3. **Tag priority.** The first configured tag that was reported is used. Tags are never added or averaged.
4. **Latest known value.** Within that tag, the most recent filing wins. An amendment replaces only the metrics it reports.
5. **Conflicts.** Different values for the same tag on the same filing date are left unselected and recorded.
6. **Ties.** Equal values on the same date use the highest accession number, so reruns give the same result.

| `selection_note` | Meaning |
|---|---|
| empty | No differing candidate |
| `restated` | The chosen tag has had another value in an earlier filing |
| `other_tag_differs` | A lower-priority tag has a different value |
| `review_other_tag_filed_later` | A lower-priority tag has a different value in a later filing |

**Missing values** stay empty in both exports. **Support data** (the year before the first in-scope year, and supporting balance sheet fields) is exported separately and not counted in coverage.

**Publication.** Files are written to a staging folder first. A run with any failed company goes to `data/processed/incomplete/` and never replaces a complete dataset.

Because later filings can revise earlier values, the output is not a point-in-time dataset. An earlier `--as-of` date limits which filings are used, based on what the API holds today.
