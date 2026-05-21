# Data Quality Metadata Assessment (Draft)

## Overview

Draft prototype for **metadata-focused** data quality (DQ) assessment in the AI Search pipeline.

The script answers one question: **Is extracted metadata search-ready before it is indexed in Azure AI Search?**

| In scope | Out of scope |
|----------|--------------|
| Metadata completeness, types, formats, allowed values | Document body / content quality |
| Searchability index and DQ status per record | Cleaning, enrichment, or remediation |
| Row-level issues and aggregate summary | Azure AI Search push, dashboards, ETL integration |

## Pipeline context

Engineering ETL extracts metadata from enterprise sources (for example: Workday, People, SharePoint, ServiceNow, MyLearning, PGLearn, CMS News).

A DQ layer evaluates each record against source-specific rules. Records below the agreed threshold can be routed to a separate **Cleaning** process. This prototype only **evaluates and reports**; it does not fix data.

## Design

### Common framework, source-specific profiles

The validation framework is shared across sources. Each source (and target index) defines its own profile:

- Expected metadata fields
- Field importance: `mandatory`, `recommended`, `optional`, or `not_applicable`
- Per-field weights
- Validation rules (type, allowed values, regex)
- Healthy / warning thresholds for the searchability index

Reference scores differ by source because each source exposes a different set of attributes.

### Searchability index

For every record, the script computes `searchability_index` in `[0, 1]`:

```text
searchability_index = passed_field_weight / total_applicable_field_weight
```

- **Applicable fields**: all profile rules where `importance != "not_applicable"`.
- **Passed field**: the field has no **blocking** or **warning** issues (info-level issues still count as passed for scoring).
- **Weight**: taken from the profile rule (default `1.0`).

Example weights (see `example_profile.json`):

| Field | Importance | Weight |
|-------|------------|--------|
| `source_id` | mandatory | 3.0 |
| `title` | mandatory | 3.0 |
| `url` | mandatory | 2.0 |
| `category_l1` | recommended | 1.5 |
| `owner` | recommended | 1.0 |
| `updated_at` | recommended | 1.0 |
| `language` | optional | 0.5 |

### DQ status

Each record gets one of: `healthy`, `warning`, or `failed`.

| Status | When it applies |
|--------|-----------------|
| `failed` | Any **blocking** issue, **or** `searchability_index` below `warning_threshold` |
| `healthy` | No blocking issues **and** `searchability_index >= healthy_threshold` |
| `warning` | No blocking issues **and** `searchability_index` between `warning_threshold` and `healthy_threshold` |

Default thresholds (overridable per profile):

| Threshold | Default |
|-----------|---------|
| `healthy_threshold` | 0.85 |
| `warning_threshold` | 0.65 |

### Issue severity by field importance

| Problem | Mandatory field | Recommended field | Optional field |
|---------|-----------------|-------------------|----------------|
| Missing value | blocking | warning | info |
| Wrong type / invalid allowed value / invalid format | blocking | warning | warning |

Supported checks:

- **Missing values** — `None`, `null`, `NaN`, empty or whitespace-only strings, placeholders (`N/A`, `na`, `not available`, etc.)
- **Wrong types** — `string`, `integer`, `float`, `boolean`, `url` (`http://` or `https://`), `date` (parsed via pandas)
- **Allowed values** — categorical fields must match the profile list
- **Format validation** — optional `regex_pattern` (full match)

## Inputs

### 1. Metadata CSV

One row per document/record. Column names must match `field_name` values in the profile.

Example: `example_input.csv` (includes intentional DQ edge cases).

```csv
source_id,title,url,source_type,category_l1,owner,updated_at,language
wd-001,Payroll Guide,https://example.com/payroll,workday,HR,HR Team,2026-05-20,en
```

### 2. Source DQ profile (JSON)

Defines rules and thresholds for one source/index pair.

Example: `example_profile.json`

```json
{
  "source_name": "workday",
  "index_name": "ai_search_main_index",
  "healthy_threshold": 0.85,
  "warning_threshold": 0.65,
  "rules": [
    {
      "field_name": "source_id",
      "expected_type": "string",
      "importance": "mandatory",
      "weight": 3.0
    }
  ]
}
```

Rule fields:

| Field | Required | Description |
|-------|----------|-------------|
| `field_name` | yes | Column name in the metadata CSV |
| `expected_type` | no (default `string`) | See supported types above |
| `importance` | no (default `optional`) | `mandatory`, `recommended`, `optional`, `not_applicable` |
| `weight` | no (default `1.0`) | Contribution to searchability index |
| `allowed_values` | no | List of permitted categorical values |
| `regex_pattern` | no | Full-match regex for format validation |
| `description` | no | Documentation only |

## Outputs

Written to the output directory (default: `outputs/`).

### 1. `metadata_with_searchability.csv`

Original metadata plus:

| Column | Description |
|--------|-------------|
| `searchability_index` | Weighted pass rate for applicable fields |
| `dq_status` | `healthy`, `warning`, or `failed` |
| `blocking_issues_count` | Count of blocking issues on the row |
| `warning_issues_count` | Count of warning issues on the row |

### 2. `dq_issues.csv`

One row per issue.

| Column | Description |
|--------|-------------|
| `row_index` | DataFrame index of the source record |
| `source_name` | From profile `source_name` |
| `field_name` | Field that failed validation |
| `issue_type` | e.g. `missing_value`, `wrong_type`, `invalid_allowed_value`, `invalid_format` |
| `severity` | `blocking`, `warning`, or `info` |
| `message` | Human-readable explanation |

### 3. `dq_summary.json`

Aggregated metrics for the run, including:

- `total_rows`, `healthy_rows`, `warning_rows`, `failed_rows` and row rates
- `average_searchability_index`
- `rows_with_missing_values`, `percent_rows_with_missing_values`
- `rows_with_wrong_types`, `percent_rows_with_wrong_types`
- `columns_with_missing_values`, `percent_columns_with_missing_values` (share of **evaluated** profile fields with at least one issue)
- `blocking_issues_count`, `warning_issues_count`, `info_issues_count`

## How to run

**Requirements:** Python 3.11+, `pandas`

```bash
pip install pandas
python dq_metadata_assessment.py
```

**Defaults** (see `if __name__ == "__main__"` in `dq_metadata_assessment.py`):

| Argument | Default |
|----------|---------|
| Input CSV | `example_input.csv` |
| Profile JSON | `example_profile.json` |
| Output directory | `outputs/` |

To assess other files, call `run_dq_assessment()` from Python or adjust the `__main__` block paths.

## Current limitations

Draft only. Not yet implemented:

- Duplicate / near-duplicate metadata detection
- Source-to-index filter coverage matrix
- Advanced cross-field consistency rules
- Binding to production ETL contracts and pipeline logging
- Cleaning workflow integration
- Azure AI Search indexing
- Dashboard or email reporting

## Recommended next steps

1. Confirm required metadata fields per source and target index.
2. Agree mandatory / recommended / optional classification and field weights.
3. Set source-specific `healthy_threshold` and `warning_threshold` values.
4. Decide which issue severities are blocking vs warning vs informational in production.
5. Choose downstream deliverables (CSV, JSON, HTML, dashboard, email).
6. After stakeholder alignment, embed `MetadataDQEvaluator` / `run_dq_assessment` into the ETL DQ stage.

## Cursor Development Environment

This project uses **Cursor rules** and **prompt templates** to keep AI-assisted work aligned with DQ scope, architecture boundaries, and the current preparation phase.

| Resource | Location | Purpose |
|----------|----------|---------|
| Cursor rules | `.cursor/rules/` | Always-on guidance: project context, boundaries, validation logic, code quality, phase constraints |
| Prompt templates | `cursor_prompts/` | Reusable tasks: analyze prototype, propose module structure, review profiles, validate outputs, client summary, Azure Functions plan |

### Cursor rules (`.cursor/rules/`)

| Rule file | Focus |
|-----------|--------|
| `00-project-context.mdc` | Draft DQ module purpose, pipeline placement, Searchability index, out-of-scope content quality |
| `10-architecture-boundaries.mdc` | What DQ should and should not do; Cleaning as separate process |
| `20-dq-validation-logic.mdc` | Profiles, severity, status logic, Searchability index, duplicates |
| `30-code-quality.mdc` | Python structure, testing, outputs, Azure Functions readiness |
| `40-no-production-implementation.mdc` | Preparation/design phase — no production code unless explicitly requested |

### Prompt templates (`cursor_prompts/`)

| Template | Use when |
|----------|----------|
| `analyze-prototype.md` | Reviewing `dq_metadata_assessment.py` and examples without refactoring |
| `propose-module-structure.md` | Designing future package layout and contracts |
| `review-dq-profile.md` | Validating `example_profile.json` or a source-specific profile |
| `validate-sample-output.md` | Checking `outputs/` after a run |
| `prepare-client-summary.md` | Drafting stakeholder-facing explanation |
| `prepare-azure-functions-plan.md` | High-level Azure Functions integration plan (document only) |

Open a template in Cursor and paste or reference it in chat to run a guided review.

### Current phase

Work in this folder is focused on **preparation and client alignment** — analysis, documentation, profile review, and integration planning.

**Production implementation should not start** until:

- Validation checks and severity rules are approved
- Source/index profiles are confirmed with source owners
- **Searchability index** field weights and **healthy/warning thresholds** are agreed
- Reporting format (CSV, JSON, optional HTML/dashboard) is confirmed
- Azure Functions integration pattern (trigger, payload, report storage, Cleaning signal) is agreed

Until then, use the prompt templates and rules above rather than expanding scope into ETL, Cleaning, or Azure AI Search automatically.
