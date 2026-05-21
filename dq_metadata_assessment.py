"""
Draft Data Quality Metadata Assessment script.

Purpose:
- Evaluate metadata quality from the search perspective.
- Calculate Searchability index per document/record.
- Identify missing values, wrong types, invalid formats, and metadata consistency issues.
- Produce DQ summary and row-level issue report.

Important:
- This script does NOT evaluate document content quality.
- This script does NOT clean or fix data.
- Cleaning/remediation should be handled by a separate process.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from typing import Any, Literal

import pandas as pd


DQSeverity = Literal["blocking", "warning", "info"]
FieldImportance = Literal["mandatory", "recommended", "optional", "not_applicable"]
DQStatus = Literal["healthy", "warning", "failed"]


MISSING_PLACEHOLDERS = {"", "nan", "none", "null", "na", "n/a", "not available"}


@dataclass
class FieldRule:
    """
    Validation rule for one metadata field.
    """

    field_name: str
    expected_type: str = "string"
    importance: FieldImportance = "optional"
    weight: float = 1.0
    allowed_values: list[str] | None = None
    regex_pattern: str | None = None
    description: str | None = None


@dataclass
class SourceDQProfile:
    """
    Source-specific metadata validation profile.

    The framework is common for all sources, but each source has its own
    expected metadata fields, weights, and thresholds.
    """

    source_name: str
    index_name: str
    rules: list[FieldRule]
    healthy_threshold: float = 0.85
    warning_threshold: float = 0.65


@dataclass
class DQIssue:
    """
    One validation issue found in one record.
    """

    row_index: int
    source_name: str
    field_name: str
    issue_type: str
    severity: DQSeverity
    message: str


@dataclass
class DQRecordResult:
    """
    Data Quality result for one row/document.
    """

    row_index: int
    searchability_index: float
    dq_status: DQStatus
    blocking_issues_count: int
    warning_issues_count: int
    info_issues_count: int
    issues: list[DQIssue] = field(default_factory=list)


class MetadataDQEvaluator:
    """
    Evaluates metadata quality from the AI Search perspective.
    """

    def __init__(self, profile: SourceDQProfile):
        self.profile = profile

    def evaluate_dataframe(self, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
        """
        Evaluate all records in the dataframe.

        Returns:
            enriched_df:
                Original dataframe with Searchability index and DQ status columns.
            issues_df:
                Row-level DQ issues.
            summary:
                Aggregated DQ metrics.
        """
        record_results: list[DQRecordResult] = []

        for row_index, row in df.iterrows():
            result = self.evaluate_record(row_index=row_index, record=row.to_dict())
            record_results.append(result)

        enriched_df = df.copy()
        enriched_df["searchability_index"] = [
            result.searchability_index for result in record_results
        ]
        enriched_df["dq_status"] = [
            result.dq_status for result in record_results
        ]
        enriched_df["blocking_issues_count"] = [
            result.blocking_issues_count for result in record_results
        ]
        enriched_df["warning_issues_count"] = [
            result.warning_issues_count for result in record_results
        ]

        all_issues = [
            issue
            for result in record_results
            for issue in result.issues
        ]

        issues_df = pd.DataFrame([issue.__dict__ for issue in all_issues])

        summary = self.build_summary(
            df=df,
            record_results=record_results,
            issues=all_issues,
        )

        return enriched_df, issues_df, summary

    def evaluate_record(self, row_index: int, record: dict[str, Any]) -> DQRecordResult:
        """
        Evaluate one metadata record.
        """
        issues: list[DQIssue] = []
        score_numerator = 0.0
        score_denominator = 0.0

        for rule in self.profile.rules:
            if rule.importance == "not_applicable":
                continue

            field_value = record.get(rule.field_name)

            field_issues = self.validate_field(
                row_index=row_index,
                rule=rule,
                value=field_value,
            )
            issues.extend(field_issues)

            score_denominator += rule.weight

            if not self.has_blocking_or_warning_issue(field_issues):
                score_numerator += rule.weight

        searchability_index = (
            score_numerator / score_denominator
            if score_denominator > 0
            else 0.0
        )

        blocking_count = sum(issue.severity == "blocking" for issue in issues)
        warning_count = sum(issue.severity == "warning" for issue in issues)
        info_count = sum(issue.severity == "info" for issue in issues)

        dq_status = self.calculate_status(
            searchability_index=searchability_index,
            blocking_issues_count=blocking_count,
        )

        return DQRecordResult(
            row_index=row_index,
            searchability_index=round(searchability_index, 4),
            dq_status=dq_status,
            blocking_issues_count=blocking_count,
            warning_issues_count=warning_count,
            info_issues_count=info_count,
            issues=issues,
        )

    def validate_field(
        self,
        row_index: int,
        rule: FieldRule,
        value: Any,
    ) -> list[DQIssue]:
        """
        Validate one metadata field according to one field rule.
        """
        issues: list[DQIssue] = []

        if self.is_missing(value):
            if rule.importance == "mandatory":
                severity: DQSeverity = "blocking"
            elif rule.importance == "recommended":
                severity = "warning"
            else:
                severity = "info"

            issues.append(
                DQIssue(
                    row_index=row_index,
                    source_name=self.profile.source_name,
                    field_name=rule.field_name,
                    issue_type="missing_value",
                    severity=severity,
                    message=f"Field '{rule.field_name}' is missing/null/empty.",
                )
            )
            return issues

        if not self.is_expected_type(value, rule.expected_type):
            severity = "blocking" if rule.importance == "mandatory" else "warning"
            issues.append(
                DQIssue(
                    row_index=row_index,
                    source_name=self.profile.source_name,
                    field_name=rule.field_name,
                    issue_type="wrong_type",
                    severity=severity,
                    message=(
                        f"Field '{rule.field_name}' has invalid type. "
                        f"Expected: {rule.expected_type}, actual value: {value!r}."
                    ),
                )
            )

        if rule.allowed_values is not None:
            normalized_value = str(value).strip()
            if normalized_value not in rule.allowed_values:
                severity = "blocking" if rule.importance == "mandatory" else "warning"
                issues.append(
                    DQIssue(
                        row_index=row_index,
                        source_name=self.profile.source_name,
                        field_name=rule.field_name,
                        issue_type="invalid_allowed_value",
                        severity=severity,
                        message=(
                            f"Field '{rule.field_name}' has value '{normalized_value}', "
                            f"but expected one of: {rule.allowed_values}."
                        ),
                    )
                )

        if rule.regex_pattern is not None:
            normalized_value = str(value).strip()
            if not re.fullmatch(rule.regex_pattern, normalized_value):
                severity = "blocking" if rule.importance == "mandatory" else "warning"
                issues.append(
                    DQIssue(
                        row_index=row_index,
                        source_name=self.profile.source_name,
                        field_name=rule.field_name,
                        issue_type="invalid_format",
                        severity=severity,
                        message=(
                            f"Field '{rule.field_name}' does not match expected format."
                        ),
                    )
                )

        return issues

    def calculate_status(
        self,
        searchability_index: float,
        blocking_issues_count: int,
    ) -> DQStatus:
        """
        Calculate high-level DQ status.
        """
        if blocking_issues_count > 0:
            return "failed"

        if searchability_index >= self.profile.healthy_threshold:
            return "healthy"

        if searchability_index >= self.profile.warning_threshold:
            return "warning"

        return "failed"

    def build_summary(
        self,
        df: pd.DataFrame,
        record_results: list[DQRecordResult],
        issues: list[DQIssue],
    ) -> dict[str, Any]:
        """
        Build aggregated DQ summary.
        """
        total_rows = len(df)

        if total_rows == 0:
            return {
                "source_name": self.profile.source_name,
                "index_name": self.profile.index_name,
                "total_rows": 0,
                "message": "No rows to evaluate.",
            }

        healthy_count = sum(result.dq_status == "healthy" for result in record_results)
        warning_count = sum(result.dq_status == "warning" for result in record_results)
        failed_count = sum(result.dq_status == "failed" for result in record_results)

        rows_with_missing_values = {
            issue.row_index
            for issue in issues
            if issue.issue_type == "missing_value"
        }

        rows_with_wrong_types = {
            issue.row_index
            for issue in issues
            if issue.issue_type == "wrong_type"
        }

        fields_with_missing_values = {
            issue.field_name
            for issue in issues
            if issue.issue_type == "missing_value"
        }

        fields_with_wrong_types = {
            issue.field_name
            for issue in issues
            if issue.issue_type == "wrong_type"
        }

        evaluated_fields = [
            rule.field_name
            for rule in self.profile.rules
            if rule.importance != "not_applicable"
        ]

        avg_searchability_index = sum(
            result.searchability_index for result in record_results
        ) / total_rows

        return {
            "source_name": self.profile.source_name,
            "index_name": self.profile.index_name,
            "total_rows": total_rows,
            "evaluated_fields_count": len(evaluated_fields),
            "healthy_rows": healthy_count,
            "warning_rows": warning_count,
            "failed_rows": failed_count,
            "healthy_rows_rate": round(healthy_count / total_rows, 4),
            "warning_rows_rate": round(warning_count / total_rows, 4),
            "failed_rows_rate": round(failed_count / total_rows, 4),
            "average_searchability_index": round(avg_searchability_index, 4),
            "rows_with_missing_values": len(rows_with_missing_values),
            "rows_with_wrong_types": len(rows_with_wrong_types),
            "percent_rows_with_missing_values": round(
                len(rows_with_missing_values) / total_rows, 4
            ),
            "percent_rows_with_wrong_types": round(
                len(rows_with_wrong_types) / total_rows, 4
            ),
            "columns_with_missing_values": len(fields_with_missing_values),
            "columns_with_wrong_types": len(fields_with_wrong_types),
            "percent_columns_with_missing_values": round(
                len(fields_with_missing_values) / max(len(evaluated_fields), 1), 4
            ),
            "percent_columns_with_wrong_types": round(
                len(fields_with_wrong_types) / max(len(evaluated_fields), 1), 4
            ),
            "blocking_issues_count": sum(issue.severity == "blocking" for issue in issues),
            "warning_issues_count": sum(issue.severity == "warning" for issue in issues),
            "info_issues_count": sum(issue.severity == "info" for issue in issues),
        }

    @staticmethod
    def is_missing(value: Any) -> bool:
        """
        Detect missing/null/empty metadata values.
        """
        if value is None:
            return True

        if pd.isna(value):
            return True

        normalized = str(value).strip().lower()
        return normalized in MISSING_PLACEHOLDERS

    @staticmethod
    def is_expected_type(value: Any, expected_type: str) -> bool:
        """
        Basic runtime type validation.
        """
        if expected_type == "string":
            return isinstance(value, str)

        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)

        if expected_type == "float":
            return isinstance(value, float) or isinstance(value, int)

        if expected_type == "boolean":
            return isinstance(value, bool)

        if expected_type == "url":
            if not isinstance(value, str):
                return False
            return value.startswith("http://") or value.startswith("https://")

        if expected_type == "date":
            try:
                pd.to_datetime(value)
                return True
            except Exception:
                return False

        return True

    @staticmethod
    def has_blocking_or_warning_issue(issues: list[DQIssue]) -> bool:
        """
        Check if field has blocking/warning issues that should reduce score.
        """
        return any(issue.severity in {"blocking", "warning"} for issue in issues)


def load_profile(profile_path: str | Path) -> SourceDQProfile:
    """
    Load source/index-specific DQ profile from JSON.
    """
    with open(profile_path, "r", encoding="utf-8") as file:
        raw_profile = json.load(file)

    rules = [FieldRule(**rule) for rule in raw_profile["rules"]]

    return SourceDQProfile(
        source_name=raw_profile["source_name"],
        index_name=raw_profile["index_name"],
        rules=rules,
        healthy_threshold=raw_profile.get("healthy_threshold", 0.85),
        warning_threshold=raw_profile.get("warning_threshold", 0.65),
    )

def read_metadata_file(input_path: str | Path) -> pd.DataFrame:
    """
    Read metadata file.

    Supports:
    - comma-separated CSV
    - tab-separated CSV/TSV
    - rows saved as one quoted field per line (common Excel/export mistake)
    """
    input_path = Path(input_path)

    try:
        df = pd.read_csv(input_path)
    except Exception:
        return pd.read_csv(input_path, sep="\t")

    if len(df.columns) == 1:
        column_name = str(df.columns[0])
        if "\t" in column_name:
            return pd.read_csv(input_path, sep="\t")
        if "," in column_name:
            # Entire row was quoted, e.g. "a,b,c" -> one column named "a,b,c".
            lines = input_path.read_text(encoding="utf-8").splitlines()
            normalized_lines = [
                line.strip().strip('"').strip("'") for line in lines if line.strip()
            ]
            return pd.read_csv(StringIO("\n".join(normalized_lines)))

    return df

def run_dq_assessment(
    input_path: str | Path,
    profile_path: str | Path,
    output_dir: str | Path,
) -> None:
    """
    Run metadata DQ assessment and save outputs.
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    profile = load_profile(profile_path)

    df = read_metadata_file(input_path)

    evaluator = MetadataDQEvaluator(profile)
    enriched_df, issues_df, summary = evaluator.evaluate_dataframe(df)

    enriched_df.to_csv(output_dir / "metadata_with_searchability.csv", index=False)
    issues_df.to_csv(output_dir / "dq_issues.csv", index=False)

    with open(output_dir / "dq_summary.json", "w", encoding="utf-8") as file:
        json.dump(summary, file, indent=2)

    print("DQ assessment completed.")
    print(f"Source: {profile.source_name}")
    print(f"Rows: {summary['total_rows']}")
    print(f"Average Searchability index: {summary['average_searchability_index']}")
    print(f"Healthy rows: {summary['healthy_rows']}")
    print(f"Warning rows: {summary['warning_rows']}")
    print(f"Failed rows: {summary['failed_rows']}")


if __name__ == "__main__":
    run_dq_assessment(
        input_path="example_input.csv",
        profile_path="example_profile.json",
        output_dir="outputs",
    )