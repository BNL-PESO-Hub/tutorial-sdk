"""Validation report models."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ValidationCheck(BaseModel):
    """A single validation check result."""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    message: str


class ValidationReport(BaseModel):
    """Machine-readable tutorial validation report."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    checks: list[ValidationCheck] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @classmethod
    def combine(
        cls,
        reports: list["ValidationReport"],
    ) -> "ValidationReport":
        """Combine several reports into one report.

        Args:
            reports: List of reports to merge.

        Returns:
            A single ``ValidationReport`` with all
            checks, errors, and warnings aggregated.
        """

        checks: list[ValidationCheck] = []
        errors: list[str] = []
        warnings: list[str] = []
        for report in reports:
            checks.extend(report.checks)
            errors.extend(report.errors)
            warnings.extend(report.warnings)
        return cls(
            passed=all(report.passed for report in reports),
            checks=checks,
            errors=errors,
            warnings=warnings,
        )

    def render_json(self) -> str:
        """Render report as stable JSON.

        Returns:
            Pretty-printed JSON string.
        """

        return json.dumps(self.model_dump(mode="json"), indent=2) + "\n"

    def write(
        self,
        path: str | Path,
    ) -> None:
        """Write report JSON to disk.

        Args:
            path: Destination file path.
        """

        Path(path).write_text(self.render_json())
