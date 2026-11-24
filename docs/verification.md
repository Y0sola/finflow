# Source verification

## Phase 1: are the metrics available? (as of 2026-11-24, data retrieved the same day)

Ten companies, fiscal years 2022 to 2025. Fiscal 2022 is prior-year support only.

| | FY2023 to 2025 (in scope) | FY2022 to 2025 |
|---|---|---|
| Expected core slots | 210 | 280 |
| Fact found | 198 (94.3%) | 264 (94.3%) |
| Missing | 12 | 16 |
| Several tags that agree | 5 | 7 |
| Several tags that disagree | 3 | 4 |
| Value differs between filings | 3 | 4 |
| One tag, one value | 187 | 249 |

This is availability, not accuracy. Final values are selected in Phase 2.

### Findings in the real data

1. **Total liabilities is missing for AMZN, KO and WMT** (3 years each in scope). Their balance sheets list
   the individual liabilities but print no total. Checked in one filing each.
2. **Parent shareholders' equity is missing for PG** (3 years in scope). PG prints total equity including
   noncontrolling interest only.
3. **Walmart's revenue tags disagree.** For fiscal 2025, `Revenues` is $680,985m and
   `RevenueFromContractWithCustomerExcludingAssessedTax` is $674,538m. The difference, $6,447m, is the printed
   "Membership and other income" line. `Revenues` is therefore first in the tag order.
4. **Diluted EPS changes after stock splits.** NVIDIA fiscal 2023 was $1.74 in the original filing and
   $0.17 after its ten-for-one split. Walmart fiscal 2023 was $4.27, later restated to $1.42 for its three-for-one split.
   NVIDIA fiscal 2022 ($3.85) was not restated in the data, so it is not comparable with later years.
5. **Revenue tags change over time.** Alphabet's fiscal 2025 value is under `Revenues` only, while fiscal 2022
   is under the contract revenue tag only.

### Accounting checks on available data

- Assets = liabilities + total equity + redeemable noncontrolling interest could be evaluated for 28 company-years.
  27 agree exactly. PG fiscal 2025 differs by $1m, consistent with rounding in the filing.
- Total equity minus noncontrolling interest equals reported parent equity in all 12 company-years where
  all three are reported.
- Total liabilities calculated from the printed lines equals total liabilities and equity minus equity
  (and redeemable noncontrolling interest): WMT FY2025 $163,131m, KO FY2024 $74,177m, AMZN FY2024 $338,924m.
  This shows a derivation is possible. No derived value is used.

## Sample comparison with 10-K documents

Figures were taken from the text of each 10-K's financial statements and compared by script with the values
in the inspection output. The seven core metrics were checked for nine company-years.

| Company | Fiscal year | 10-K | Printed values matched | Not printed and not in data |
|---|---|---|---|---|
| AAPL | 2024 | [10-K](https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm) | 7 of 7 | 0 |
| WMT | 2025 | [10-K](https://www.sec.gov/Archives/edgar/data/104169/000010416925000021/wmt-20250131.htm) | 6 of 6 | total liabilities |
| KO | 2024 | [10-K](https://www.sec.gov/Archives/edgar/data/21344/000002134425000011/ko-20241231.htm) | 6 of 6 | total liabilities |
| AMZN | 2024 | [10-K](https://www.sec.gov/Archives/edgar/data/1018724/000101872425000004/amzn-20241231.htm) | 6 of 6 | total liabilities |
| PG | 2025 | [10-K](https://www.sec.gov/Archives/edgar/data/80424/000008042425000076/pg-20250630.htm) | 6 of 6 | parent equity |
| TSLA | 2024 | [10-K](https://www.sec.gov/Archives/edgar/data/1318605/000162828025003063/tsla-20241231.htm) | 7 of 7 | 0 |
| NVDA | 2023 | [10-K](https://www.sec.gov/Archives/edgar/data/1045810/000104581023000017/nvda-20230129.htm), EPS from the [FY2025 10-K](https://www.sec.gov/Archives/edgar/data/1045810/000104581025000023/nvda-20250126.htm) | 7 of 7 | 0 |
| NVDA | 2025 | [10-K](https://www.sec.gov/Archives/edgar/data/1045810/000104581025000023/nvda-20250126.htm) | 7 of 7 | 0 |
| GOOGL | 2025 | [10-K](https://www.sec.gov/Archives/edgar/data/1652044/000165204426000018/goog-20251231.htm) | 7 of 7 | 0 |

Total: 59 of 59 printed values matched, and 4 of 4 absent values were absent in both. For Walmart, revenue was
compared with the `Revenues` tag.

**Not yet checked:** META and MSFT filings, the other fiscal years, and a full two-company comparison across all
three years, which belongs to Phase 3.
