# Data Quality Metadata Assessment (Draft)

## Overview

Draft for **metadata-focused** data quality (DQ) assessment in the AI Search pipeline.

The script answers one question: **Is extracted metadata search-ready before it is indexed in Azure AI Search?**

| In scope | Out of scope |
|----------|--------------|
| Metadata completeness, types, formats, allowed values | Document body / content quality |
| Searchability index and DQ status per record | Cleaning, enrichment, or remediation |
| Row-level issues and aggregate summary | Azure AI Search push, dashboards, ETL integration |

## Pipeline context

Engineering ETL extracts metadata from enterprise sources into a combined common schema. The production combined file is **`combined_common.csv`**.

**Current active sources** (`source_type`):

| Source | Approx. row count |
|--------|-------------------|
| `my_learning` | 34,930 |
| `pglearn` | 18,075 |
| `servicenow` | 4,802 |
| `quicklinks` | 1,323 |
| `apps` | 595 |
| `cms_news` | 198 |

The previous **Workday** placeholder example has been removed. Sample files in this repo align with the six sources above. Production DQ should use **source-specific profiles** per `source_type` because metadata availability differs by source.

A DQ layer evaluates each record against source-specific rules. Records below the agreed threshold can be routed to a separate **Cleaning** process. This prototype only **evaluates and reports**; it does not fix data.

**Combined common schema (17 columns):** `combined_record_id`, `source_id`, `source_type`, `title`, `description`, `text`, `text_clean`, `category_l1`, `category_l2`, `category_l3`, `status`, `url`, `owner`, `updated_at`, `quality_flags`, `is_search_eligible`, `search_eligibility_reason`.

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

Example weights (draft `combined_common_sample` profile in `example_profile.json`):

| Field | Importance | Weight |
|-------|------------|--------|
| `combined_record_id` | mandatory | 3.0 |
| `source_id` | mandatory | 3.0 |
| `source_type` | mandatory | 2.0 |
| `title` | mandatory | 3.0 |
| `text_clean` | mandatory | 3.0 |
| `description` | recommended | 1.5 |
| `url` | recommended | 1.5 |
| `category_l1` | recommended | 1.5 |
| `updated_at` | recommended | 1.0 |
| `quality_flags` | recommended | 0.5 |
| `category_l2`, `category_l3`, `status`, `owner`, etc. | optional | 0.25–0.5 |

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

Example: `example_input.csv` (12 rows across six sources; includes intentional DQ edge cases).

Uses the full combined common-schema header. Production assessments should use `combined_common.csv` or a source-filtered export with the same columns.

### 2. Source DQ profile (JSON)

Defines rules and thresholds for one source/index pair (or a draft combined sample for demos).

Example: `example_profile.json` — `source_name`: **`combined_common_sample`** (draft only; not for production scoring of all sources with one profile).

```json
{
  "source_name": "combined_common_sample",
  "index_name": "ai_search_main_index",
  "healthy_threshold": 0.85,
  "warning_threshold": 0.65,
  "rules": [
    {
      "field_name": "combined_record_id",
      "expected_type": "string",
      "importance": "mandatory",
      "weight": 3.0
    },
    {
      "field_name": "source_type",
      "expected_type": "string",
      "importance": "mandatory",
      "weight": 2.0,
      "allowed_values": ["servicenow", "apps", "quicklinks", "my_learning", "pglearn", "cms_news"]
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

**Dataset alignment:** Cursor rules and `cursor_prompts/` reference the six active sources in `combined_common.csv` (ServiceNow, Apps, Quicklinks, MyLearning, PGLearn, CMS News). Workday is not used as an active example. `example_profile.json` is a draft combined common-schema profile for local demos; production should use per-`source_type` profiles.

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

Update...