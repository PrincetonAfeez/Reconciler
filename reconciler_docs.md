# Architecture Decision Record
## App 12 — Reconciler
**Ledger Logic Group | Document 1 of 5**
**Status: Accepted**

---

## Context

The Reconciler is the twelfth app in the portfolio and the fifth in the Ledger Logic group. It compares two transaction CSV files — a source (e.g., bank export) and a reference (e.g., personal ledger) — and categorizes every row as matched, amount mismatch, date mismatch, suspicious, or unmatched. It also detects exact and near-duplicate transactions within each file. The module was identified during the evaluation as requiring the CLI to be extracted into `reconciler_cli.py` — that required action was completed before documentation.

---

## Decisions

### Decision 1 — Three-pass matching pipeline: exact → exact-merchant → fuzzy

**Chosen:** `reconcile()` runs three passes in sequence. Pass 1 (`exact_match_pass`) finds records identical on all three fields. Pass 2 (`exact_merchant_pass`) finds same-merchant records with small date or amount discrepancies. Pass 3 (`fuzzy_match_pass`) applies Levenshtein-based name matching to the remainder.

**Rejected:** A single pass with configurable tolerances.

**Reason:** A single-pass approach forces every record through fuzzy matching even when an exact match exists. The three-pass design respects a natural quality hierarchy: exact matches are definitive, same-merchant discrepancies are investigated, and fuzzy matches are last-resort. It also prevents over-matching — once a record is consumed in Pass 1, it cannot be incorrectly re-matched in Pass 3. The `used_source` and `used_reference` sets are passed through all three passes, making consumption tracking explicit.

---

### Decision 2 — Integer cents for amount comparison

**Chosen:** Every `ReconciliationRecord` stores `amount_cents: int` alongside `amount: float`. All comparisons use `amount_cents`. The `cents(float) → int` helper applies `round(amount * 100)`.

**Rejected:** Comparing float amounts directly.

**Reason:** Float comparison with a tolerance like `abs(a - b) < 0.50` produces unreliable results when amounts have binary float representation errors. `abs(14.73 - 14.73)` may not be exactly `0.0` in Python float arithmetic. Integer cents are exact. `abs(1473 - 1473) == 0` is guaranteed. The `amount_tolerance` in dollars is converted to `amount_tolerance_cents = cents(amount_tolerance)` once at the start of `reconcile()` and used throughout.

---

### Decision 3 — `build_confidence()` with weighted three-field score

**Chosen:** `confidence = name_similarity × 0.50 + date_component × 0.25 + amount_component × 0.25`. Date and amount components are scaled relative to their configured tolerances.

**Rejected:** A binary pass/fail per field.

**Reason:** A pair where merchant names are identical but dates differ by 1 day should have a higher confidence than a pair where merchant names have a high but non-perfect fuzzy score. The weighted formula produces a single comparable scalar that ranks candidate pairs against each other when multiple candidates are available for the same source record.

---

### Decision 4 — `detect_duplicates()` with exact and near-duplicate detection

**Chosen:** Within-file duplicate detection runs before cross-file comparison. Exact duplicates: `(merchant_key, amount_cents, date)` key appears more than once. Near-duplicates: same `(merchant_key, amount_cents)` key, sorted by date, consecutive entries within `near_duplicate_days` (default 2).

**Rejected:** Only detecting exact duplicates.

**Reason:** Real bank exports frequently contain near-duplicate entries — a pending and posted version of the same charge, or two coffee purchases on consecutive days at the same merchant for the same amount. Near-duplicate detection catches these for human review without requiring them to be exact. The result appears in the `Duplicates` section of the report.

---

### Decision 5 — `reconciler_cli.py` extracted from `reconciler.py`

**Chosen:** All `argparse`, `logging`, CLI menu, and `run_cli_args()` logic lives in `reconciler_cli.py`. `reconciler.py` delegates `menu()`, `_build_arg_parser()`, `run_cli_args()`, and `main()` to `reconciler_cli` via lazy imports.

**Rejected:** CLI code inside `reconciler.py`.

**Reason:** This was the required action from the evaluation session. `reconciler.py` imports `argparse` and `logging` inside `main()` as deferred imports — keeping the module importable as a library without triggering CLI infrastructure. The lazy import pattern (`from reconciler_cli import menu as cli_menu`) means `reconciler_cli` is only loaded when a CLI function is actually called.

---

### Decision 6 — `textutil.similarity_ratio()` with `min_ratio` early-exit optimization

**Chosen:** `similarity_ratio()` in the reconciler's `textutil.py` accepts an optional `min_ratio` parameter. When set, `_levenshtein_distance_capped()` short-circuits when the distance already exceeds the acceptable threshold.

**Rejected:** Always computing full Levenshtein distance.

**Reason:** The fuzzy pass in `reconcile()` is O(n²) — every unmatched source record against every unmatched reference record. For a 100-row source vs 100-row reference, that is up to 10,000 similarity computations. The `min_ratio` early exit avoids computing the full distance matrix when the length difference alone shows the strings cannot possibly score above the threshold.

---

### Decision 7 — Set-based summary alongside pair-based report

**Chosen:** `reconcile()` computes `set_summary` using Python set operations on `(date, merchant_key)` tuples: `shared_keys`, `source_only_keys`, `reference_only_keys`, `symmetric_difference`. These are reported alongside the pair-based match buckets.

**Rejected:** Only reporting pair-based counts.

**Reason:** Set membership gives a different view of the reconciliation than pair matching. Two source records for the same merchant/date pair count as 2 pairs but share 1 set key. The set summary helps operators understand structural overlap between the files — "40 shared keys" means 40 distinct merchant/date combinations appear in both files, regardless of how many duplicate rows there are per combination.

---

## Consequences

**Positive:**
- Three-pass pipeline prevents over-matching and respects quality hierarchy.
- Integer cents eliminate float comparison unreliability.
- Weighted confidence score ranks candidates for the same source record.
- Near-duplicate detection catches pending/posted transaction pairs.
- `reconciler_cli.py` extraction keeps engine importable as a library.
- `min_ratio` optimization reduces O(n²) fuzzy matching cost.

**Negative / Trade-offs:**
- The fuzzy pass is still O(n²) in the worst case — every unmatched source record against every unmatched reference record. For files with thousands of unmatched rows this becomes slow.
- `match_rate` is defined as `matched_pairs / max(source_count, reference_count)`. This is a coverage fraction of the larger file, not precision or recall. It is not directly comparable to traditional IR metrics.
- The three-pass pipeline is greedy — it does not find the globally optimal matching. Two source records that both fuzzy-match a single reference record will not both match; the first one wins. A maximum bipartite matching algorithm would produce globally optimal results.

---

*Constitution reference: Articles 1, 2, 3. Required action completed: CLI extracted to `reconciler_cli.py`. Amendment 1.3: `parsing.py`, `schemas.py`, `storage.py` are pinned snapshots.*


---


# Technical Design Document
## App 12 — Reconciler
**Ledger Logic Group | Document 2 of 5**

---

## Overview

The Reconciler compares two transaction CSV files row by row using a three-pass matching pipeline (exact, exact-merchant, fuzzy), produces a categorized report with six output buckets, detects within-file duplicates, and supports both interactive and argparse CLI operation.

**Files:** `reconciler.py` (814 lines), `reconciler_cli.py` (argparse + menu)
**Supporting:** `csv_columns.py` (weighted column detection), `textutil.py` (with `min_ratio`)
**Shared (pinned snapshots):** `parsing.py`, `schemas.py`, `storage.py`
**Entry points:** `reconciler.main()` → `reconciler_cli.main()`, or `run_reconciliation()` for library use
**Dependencies:** `csv`, `logging`, `datetime`, `pathlib`, `argparse` (stdlib); `csv_columns`, `textutil`, `parsing`, `schemas`, `storage`

---

## Data Flow

```
source_file, reference_file (or mock mode)
        │
        ▼
load_transactions(file, label)
        ├─ detect_columns(headers, column_map)  →  {date, merchant, amount}
        └─ For each row:
               ├─ parse_date() → date
               ├─ parse_amount() → float
               ├─ clean_text(merchant) → merchant_key
               └─ cents(amount) → amount_cents
        │
        ▼
list[ReconciliationRecord]  ×2 (source, reference)
        │
        ├─ detect_duplicates(source) → DuplicateDetectionResult
        ├─ detect_duplicates(reference) → DuplicateDetectionResult
        │
        └─ reconcile(source, reference, thresholds)
               ├─ Pass 1: exact_match_pass()
               │     └─ dict lookup on (date, merchant_key, amount_cents)
               ├─ Pass 2: exact_merchant_pass()
               │     └─ merchant_key lookup → build_confidence() → classify
               └─ Pass 3: fuzzy_match_pass()
                     └─ similarity_ratio(min_ratio=threshold) → build_confidence() → classify
        │
        ▼
ReconciliationReport {matched, amount_mismatch, date_mismatch, suspicious,
                      unmatched_source, unmatched_reference, set_summary, ...}
        │
        ▼
build_report_text(report, dup_source, dup_reference) → str
        │
        ▼
RunReconciliationResult {report, report_text, warnings, duplicate_source,
                         duplicate_reference, output_path}
```

---

## `ReconciliationRecord` Schema

```python
{
    "date": date,              # Parsed date object
    "merchant": str,           # Raw merchant name (display)
    "merchant_key": str,       # clean_text(merchant) — used for all comparisons
    "amount": float,           # Rounded to 2 decimal places
    "amount_cents": int,       # round(amount × 100)
    "source_label": str,       # "Source" or "Reference"
    "line_number": int,        # 1-based CSV row number
}
```

---

## `ReconciliationPair` Schema

```python
{
    "source": ReconciliationRecord,
    "reference": ReconciliationRecord,
    "confidence": float,       # 0.0–1.0 from build_confidence()
    "reason": str,             # "exact" | "exact merchant" | "fuzzy merchant"
    "amount_delta": float,     # source.amount - reference.amount
    "date_gap": int,           # abs(source.date - reference.date).days
}
```

---

## Three-Pass Pipeline Detail

### Pass 1: `exact_match_pass()`
**Key:** `(date, merchant_key, amount_cents)`
**Method:** Build dict from reference records. For each source record, look up key. If found, consume both records with `confidence=1.0`, reason `"exact"`.

### Pass 2: `exact_merchant_pass()`
**Key:** `merchant_key`
**Method:** Group remaining reference records by `merchant_key`. For each remaining source record, check candidates from same merchant group. Pick highest-`build_confidence()` candidate. Classify as matched/amount_mismatch/date_mismatch/suspicious based on tolerance checks.

### Pass 3: `fuzzy_match_pass()`
**Method:** For each remaining source record, compare against all remaining reference records using `similarity_ratio(min_ratio=fuzzy_threshold)`. Skip pairs where similarity < threshold. Pick highest-confidence candidate. Classify same as Pass 2.

### Match Classification Per Pass (2 and 3)

| Date gap | Amount gap | Classification |
|---|---|---|
| ≤ tolerance | ≤ tolerance | `matched` |
| ≤ tolerance | > tolerance | `amount_mismatch` |
| > tolerance | ≤ tolerance | `date_mismatch` |
| > tolerance | > tolerance | `suspicious` |

---

## `build_confidence()` Formula

```python
date_component = max(0.0, 1.0 - date_gap / (date_tolerance + 1))
amount_component = max(0.0, 1.0 - amount_gap_cents / (amount_tolerance_cents × 3))
confidence = name_similarity × 0.50 + date_component × 0.25 + amount_component × 0.25
```

---

## `detect_duplicates()` Logic

**Exact duplicates:** Group by `(merchant_key, amount_cents, date)`. Any group with > 1 record is an exact duplicate cluster.

**Near-duplicates:** Group by `(merchant_key, amount_cents)`. Sort each group by date. Any consecutive pair where `date_gap ≤ near_duplicate_days` (default 2) is a near-duplicate.

---

## `csv_columns.py` — Reconciler Version

This module uses **additive weighted scoring** (not permutation-based). `_score_date()`, `_score_merchant()`, `_score_amount()` each check a list of keyword patterns with weights. Multi-keyword headers accumulate points.

Examples:
- `"Transaction Description"` → merchant score 14.0 (substring "transaction description")
- `"Posting Date"` → date score 12.0 (substring "posting date")
- `"Debit Amount"` → amount score 23.0 (both "debit" and "amount" match)

Assignment: greedy by role priority (date → merchant → amount) using `take_best()`. Leftover columns filled by remaining order.

---

## `ReconciliationReport` Schema

```python
{
    "matched": list[ReconciliationPair],
    "amount_mismatch": list[ReconciliationPair],
    "date_mismatch": list[ReconciliationPair],
    "suspicious": list[ReconciliationPair],
    "unmatched_source": list[ReconciliationRecord],
    "unmatched_reference": list[ReconciliationRecord],
    "set_summary": {
        "shared_keys": int,           # |(date, merchant_key) in both|
        "source_only_keys": int,
        "reference_only_keys": int,
        "symmetric_difference": int,
    },
    "source_total": float,
    "reference_total": float,
    "net_difference": float,
    "match_rate": float,              # % of larger file that matched
    "source_count": int,
    "reference_count": int,
}
```

---

## `textutil.py` — Reconciler Version

Adds `_levenshtein_distance_capped(left, right, max_dist)` and `min_ratio` parameter to `similarity_ratio()`. When `min_ratio` is set:
1. Compute `max_dist = floor((1 - min_ratio) × max_len)`
2. If `abs(len_left - len_right) > max_dist` → return `0.0` immediately
3. Run `_levenshtein_distance_capped()` with row-minimum early exit
4. If result > `max_dist` → return `0.0`


---


# Interface Design Specification
## App 12 — Reconciler
**Ledger Logic Group | Document 3 of 5**

---

## Public API

### Primary Entry Point

```python
run_reconciliation(
    source_file: str | Path | None = None,
    reference_file: str | Path | None = None,
    fuzzy_threshold: float = 0.80,
    date_tolerance: int = 2,
    amount_tolerance: float = 0.50,
    use_mock: bool = False,
    export_report: bool = False,
    output_dir: str | Path | None = None,
) -> RunReconciliationResult
```

**Parameters:**

| Parameter | Default | Description |
|---|---|---|
| `source_file` | `None` | Path to source CSV (e.g., bank export) |
| `reference_file` | `None` | Path to reference CSV (e.g., personal ledger) |
| `fuzzy_threshold` | `0.80` | Minimum similarity ratio for fuzzy merchant matching |
| `date_tolerance` | `2` | Max days between matched dates |
| `amount_tolerance` | `0.50` | Max dollar difference for matched amounts |
| `use_mock` | `False` | Use built-in mock data instead of files |
| `export_report` | `False` | Write `reconciliation_report.txt` |
| `output_dir` | `None` | Directory for exported report |

---

### CLI

```bash
# Interactive menu
python reconciler.py

# CLI with files
python reconciler.py --source bank.csv --reference ledger.csv

# CLI with mock data
python reconciler.py --mock

# Export report
python reconciler.py --source bank.csv --reference ledger.csv --export

# Custom thresholds
python reconciler.py --source bank.csv --reference ledger.csv \
  --fuzzy 85 --date-tolerance 3 --amount-tolerance 1.00

# Output dir for exported report
python reconciler.py --source bank.csv --reference ledger.csv \
  --export --output-dir reports/

# Suppress report to stdout (only export)
python reconciler.py --source bank.csv --reference ledger.csv \
  --export --quiet-report

# Verbose logging
python reconciler.py --mock --verbose
```

**Exit codes:** `0` success, `1` file/runtime error, `2` argument error.

---

## `RunReconciliationResult` Schema

```python
{
    "report": ReconciliationReport,
    "report_text": str,
    "warnings": list[str],
    "duplicate_source": DuplicateDetectionResult,
    "duplicate_reference": DuplicateDetectionResult,
    "output_path": Path | None,
}
```

---

## Input/Output Examples

### Basic reconciliation
```python
result = run_reconciliation(
    source_file="bank.csv",
    reference_file="ledger.csv",
)
report = result["report"]
print(f"Matched: {len(report['matched'])}")
print(f"Amount mismatches: {len(report['amount_mismatch'])}")
print(f"Date mismatches: {len(report['date_mismatch'])}")
print(f"Unmatched source: {len(report['unmatched_source'])}")
print(f"Net difference: ${report['net_difference']:.2f}")
print(f"Match rate: {report['match_rate']:.1f}%")
print(result["report_text"])
```

### Mock data run
```python
result = run_reconciliation(use_mock=True)
# Source: 8 rows (includes 2 exact Starbucks duplicates)
# Reference: 7 rows (includes "Shell Oil" vs "Shell", amount mismatch, near-duplicate)
# Expected: 2 matched, 2 amount_mismatch, 1 date_mismatch, 1+ unmatched
```

### Export report to file
```python
result = run_reconciliation(
    source_file="bank.csv",
    reference_file="ledger.csv",
    export_report=True,
    output_dir="reports/",
)
print(f"Report written to: {result['output_path']}")
```

### Access duplicate detection
```python
result = run_reconciliation(source_file="bank.csv", reference_file="ledger.csv")
for item in result["duplicate_source"]["exact"]:
    print(f"Exact duplicate: {item['record']['merchant']} ×{item['count']}")
for item in result["duplicate_source"]["near"]:
    print(f"Near duplicate: {item['record']['merchant']} within {item['gap']} day(s)")
```

---

## Report Text Format

```
Reconciliation Report
========================================
Source transactions: 8 | Reference transactions: 7 | Match rate (coverage vs larger file): 75.0%
Discrepancies: 6
Set summary - shared: 5, source only: 2, reference only: 2, symmetric diff: 4
Grand totals - source: $304.25, reference: $355.73, net difference: -$51.48

Matched
----------------------------------------
2026-03-01 Whole Foods $83.21  ||  2026-03-01 Whole Foods $83.21  [confidence 1.00]
2026-03-12 Walgreens $18.45    ||  2026-03-12 Walgreens $18.45    [confidence 1.00]

Amount Mismatches
----------------------------------------
2026-03-05 Starbucks $6.25  ||  2026-03-05 Starbucks $5.95  [confidence 0.88] | delta $0.30

Date Mismatches
----------------------------------------
2026-03-02 Shell $48.10  ||  2026-03-03 Shell Oil $48.10  [confidence 0.91] | date gap 1 day(s)

Suspicious Entries
----------------------------------------
...

Unmatched Source Only
----------------------------------------
2026-03-10 Starbucks $6.25
2026-03-10 Starbucks $6.25

Unmatched Reference Only
----------------------------------------
2026-03-15 Trader Joes $64.88

Duplicates
----------------------------------------
Source exact duplicate: Starbucks $6.25 x2
```

---

## Default Thresholds

| Parameter | Default | Meaning |
|---|---|---|
| `fuzzy_threshold` | 0.80 | Name similarity minimum (80%) |
| `date_tolerance` | 2 | Dates within 2 days are compatible |
| `amount_tolerance` | $0.50 | Amounts within $0.50 are compatible |
| `near_duplicate_days` | 2 | Same merchant/amount within 2 days = near-duplicate |


---


# Runbook
## App 12 — Reconciler
**Ledger Logic Group | Document 4 of 5**

---

## Requirements

- Python 3.10 or later
- No third-party dependencies
- `csv_columns.py`, `textutil.py`, `reconciler_cli.py`, `parsing.py`, `schemas.py`, `storage.py` in same directory
- `typing_extensions` for `schemas.py` (Python < 3.11)

---

## Installation

```bash
git clone https://github.com/PrincetonAfeez/ledger-logic
cd ledger-logic/reconciler
pip install typing_extensions   # Only if Python < 3.11
```

---

## Running the CLI

### Interactive menu (no args)
```bash
python reconciler.py
```

### From CLI arguments
```bash
# Compare two files
python reconciler.py --source bank_export.csv --reference my_ledger.csv

# Test with mock data
python reconciler.py --mock

# Export report file
python reconciler.py --source bank.csv --reference ledger.csv --export

# Strict thresholds
python reconciler.py --source bank.csv --reference ledger.csv \
  --fuzzy 90 --date-tolerance 1 --amount-tolerance 0.01
```

### Generate mock CSV files for testing
```bash
# From menu: option 2 (Generate mock data)
# Or from Python:
python -c "from reconciler import export_mock_csvs; export_mock_csvs()"
# Creates mock_source.csv and mock_reference.csv in CWD
```

---

## Using as a Library

### Basic comparison
```python
from reconciler import run_reconciliation

result = run_reconciliation(
    source_file="bank.csv",
    reference_file="ledger.csv",
    fuzzy_threshold=0.80,
    date_tolerance=2,
    amount_tolerance=0.50,
)
print(result["report_text"])
```

### Access specific buckets
```python
report = result["report"]

# All matched pairs
for pair in report["matched"]:
    print(f"✓ {pair['source']['merchant']} ${pair['source']['amount']:.2f}")

# Amount mismatches requiring review
for pair in report["amount_mismatch"]:
    delta = pair["amount_delta"]
    print(f"$ {pair['source']['merchant']}: delta ${delta:.2f}")

# Transactions only in source (not in reference)
for row in report["unmatched_source"]:
    print(f"! Source only: {row['merchant']} ${row['amount']:.2f}")
```

### Adjust fuzzy threshold
```python
# More lenient (catches more typos, more false positives)
result = run_reconciliation(source_file="bank.csv", reference_file="ledger.csv",
                            fuzzy_threshold=0.70)

# Stricter (fewer false positives, may miss more)
result = run_reconciliation(source_file="bank.csv", reference_file="ledger.csv",
                            fuzzy_threshold=0.90)
```

---

## Running Tests

No dedicated test file was uploaded for App 12. Manual verification:

```bash
python reconciler.py --mock
# Confirm: "Whole Foods" and "Walgreens" in Matched (exact)
# Confirm: "Starbucks" in Amount Mismatches ($6.25 vs $5.95)
# Confirm: "Shell" / "Shell Oil" in Date Mismatches (1-day gap)
# Confirm: "Trader Joes" in Unmatched Reference Only
# Confirm: Source exact duplicate for Starbucks x2
```

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'reconciler_cli'`
`reconciler_cli.py` must be in the same directory. This was the required action from the evaluation — confirm the file was added.

### High unmatched rate despite visually similar records
Check `similarity_ratio` directly:
```python
from textutil import similarity_ratio, clean_text
print(similarity_ratio(clean_text("Amazon Marketplace"), clean_text("Amazon")))
# 6/18 = 0.33 — below 0.80 threshold for whole-string comparison
# This is correct — the per-word pass in categorizer.py is not used here
```
Add an exact or near-exact merchant name in the reference file, or lower `--fuzzy` threshold.

### `match_rate` is confusingly high/low
`match_rate` is `matched / max(source_count, reference_count)`. It measures coverage of the larger file, not accuracy. A 75% match rate with 8 source and 7 reference rows means 6 of 8 rows were paired (Pass 1+2+3 combined "matched" bucket only).

### Date mismatch when dates look the same
Check the raw CSV date formats. `parse_date()` tries US format first (`MM/DD/YYYY`). Two files using different conventions (`01/02/2024` meaning Jan 2 in one and Feb 1 in the other) will produce wrong `date` objects and apparent mismatches.


---


# Lessons Learned
## App 12 — Reconciler
**Ledger Logic Group | Document 5 of 5**

---

## Why This Design Was Chosen

The three-pass architecture came from thinking about what "matching" means for financial data. A transaction that appears in both files with identical merchant, date, and amount is a confident match. A transaction with the same merchant but a $0.30 amount difference might be a rounding error or a fee. A transaction with the same merchant and amount but a 1-day date difference might be a posting lag. A transaction with a fuzzy-similar merchant name is the most uncertain. Running three passes in order — with consumption tracking — ensures that the most confident matches are resolved first and do not interfere with the harder cases.

The `used_source` and `used_reference` sets being passed by reference through all three passes was the key structural decision. Without explicit consumption tracking, Pass 3 could re-match records that Pass 1 already consumed. The set-based tracking makes the greedy nature of the algorithm explicit and auditable.

---

## What Was Intentionally Omitted

**Global optimal matching (bipartite matching):** The greedy three-pass approach does not always find the globally optimal matching. If two source records both fuzzy-match the same reference record, the first source record wins. The Hungarian algorithm or Hopcroft-Karp would find the globally optimal one-to-one matching but are significantly more complex to implement. For the sizes of financial CSV files this module targets (hundreds of rows), the greedy approach produces acceptable results.

**Multi-currency support:** All amounts are treated as the same currency. A source file in USD and a reference file in EUR would produce nonsensical amount comparisons. Multi-currency support requires a currency column, an exchange rate lookup, and conversion before comparison.

**Configurable column mapping:** `load_transactions()` accepts a `column_map` parameter for manual column override, but the CLI does not expose this option. A `--column-map` flag would allow operators to specify `date=0,merchant=2,amount=3` for unusual CSVs.

---

## The Required Action: `reconciler_cli.py`

During the evaluation session, `menu()`, `_build_arg_parser()`, `run_cli_args()`, and the `argparse`/`logging` infrastructure were all inside `reconciler.py`. The evaluation found this violated the engine/CLI separation principle established by App 06 (Password Checker) and App 08 (Budget).

The fix extracted all CLI-specific code into `reconciler_cli.py`. `reconciler.py` now delegates to `reconciler_cli` via lazy imports inside the `menu()`, `_build_arg_parser()`, `run_cli_args()`, and `main()` functions. This means:
- Importing `reconciler` as a library does not import `argparse` or `logging` at module load time
- `reconciler_cli.py` is not imported at all unless a CLI function is called
- `run_reconciliation()` remains pure library code with no CLI dependencies

---

## Biggest Weakness

The fuzzy pass is O(n²) — every remaining source record against every remaining reference record. For a file with 1,000 source rows and 1,000 reference rows where most records are unmatched (a bad data quality scenario), this is up to 1,000,000 similarity computations. The `min_ratio` early-exit optimization in `similarity_ratio()` significantly reduces the average case but does not change the worst case.

The practical mitigation is that in well-formed data, the exact and exact-merchant passes resolve most rows, leaving only a small remainder for the fuzzy pass. The O(n²) cost is proportional to the number of _unmatched_ rows after Passes 1 and 2, which is typically small.

A better approach for large files: build a candidate index by the first 3 characters of `merchant_key`, limiting fuzzy comparisons to records that share a prefix. This reduces the comparison space from O(n×m) to O(n×k) where k is the average size of a prefix bucket.

---

## Scaling Considerations

**If files grow to tens of thousands of rows:** Replace the fuzzy pass with a prefix-indexed candidate search. Build a dict mapping the first 3 characters of `merchant_key` to a list of reference record indices. Each source record only compares against records in its prefix bucket.

**If multiple currencies are needed:** Add a `currency` field to `ReconciliationRecord` and only attempt to match records with the same currency code. Amount comparison requires conversion to a common currency via an exchange rate lookup.

**If the match rate needs to be more meaningful:** Replace the current coverage-based `match_rate` with precision and recall calculated against a labeled ground-truth file. This requires having a "correct" answer to compare against.

---

## What the Next Refactor Would Be

1. **Prefix-indexed fuzzy pass** — reduce O(n²) to O(n × prefix_bucket_size).
2. **`--column-map` CLI flag** — allow `date=0,merchant=2,amount=3` overrides.
3. **Currency support** — `currency` field in `ReconciliationRecord`, match only same-currency records.
4. **Maximum bipartite matching option** — `--optimal` flag for Hungarian algorithm when exact optimality matters.

---

## What This Project Taught

**Consumption tracking is the architecture of a greedy matching algorithm.** The `used_source` and `used_reference` sets are not an implementation detail — they are the mechanism that makes the three-pass pipeline work correctly. Without them, records could be matched multiple times. With them, the algorithm is provably non-redundant: any record consumed in Pass 1 will never appear in Pass 2 or 3. Designing the pipeline around the consumption sets — rather than trying to filter results after the fact — is the correct approach.

**Float comparison for currency requires integer cents.** `abs(6.25 - 5.95) < 0.50` seems simple. In Python: `abs(6.25 - 5.95) == 0.30000000000000026`. Whether `0.30000000000000026 < 0.50` happens to be `True` in this case, but it illustrates the unreliability. Converting to cents once at input and comparing integers throughout eliminates this class of error entirely — the same lesson as App 09 (Change Maker) applied to a more complex comparison problem.

**Set-based summary gives different information than pair-based matching.** After reconciliation, `set_summary["shared_keys"]` and `len(report["matched"])` can be different numbers. The set summary answers "how many unique merchant/date combinations appear in both files?" The pair summary answers "how many rows were matched?" For files with duplicate transactions, these numbers diverge in meaningful ways. Both views are useful for understanding data quality.

---

*Constitution v2.0 checklist: This document satisfies Article 5 (trade-off documentation) for App 12.*
*Note: This document also records the required action completed during evaluation — CLI extraction into `reconciler_cli.py`.*
