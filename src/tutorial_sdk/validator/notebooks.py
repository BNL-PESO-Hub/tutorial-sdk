"""Notebook execution validation."""

import json
from pathlib import Path

from ..spec import TutorialSpec
from .report import ValidationCheck, ValidationReport


class NotebookValidator:
    """Validate notebooks declared by a tutorial."""

    def __init__(
        self,
        spec: TutorialSpec,
        root: str | Path = ".",
    ) -> None:
        """Create a notebook validator.

        Args:
            spec: Parsed tutorial specification.
            root: Tutorial project root directory.
        """

        self.spec = spec
        self.root = Path(root)

    def validate(self) -> ValidationReport:
        """Validate notebook files and stored execution errors.

        The MVP does not execute notebooks directly. It detects missing
        notebook files and error outputs already present in notebooks.
        """

        missing = [
            path
            for path in self.spec.content.notebooks
            if not (self.root / path).exists()
        ]
        if missing:
            message = "Missing notebooks: " + ", ".join(missing)
            return ValidationReport(
                passed=False,
                checks=[
                    ValidationCheck(
                        name="notebooks.present",
                        passed=False,
                        message=message,
                    )
                ],
                errors=[message],
            )

        errors = self._stored_execution_errors()
        if errors and self.spec.validation.require_clean_execution:
            message = "Notebook execution errors found: " + "; ".join(errors)
            return ValidationReport(
                passed=False,
                checks=[
                    ValidationCheck(
                        name="notebooks.clean",
                        passed=False,
                        message=message,
                    )
                ],
                errors=[message],
            )

        return ValidationReport(
            passed=True,
            checks=[
                ValidationCheck(
                    name="notebooks.present",
                    passed=True,
                    message=(
                        f"{len(self.spec.content.notebooks)} notebooks "
                        "declared and readable."
                    ),
                )
            ],
        )

    def _stored_execution_errors(self) -> list[str]:
        """Scan notebooks for stored error outputs.

        Returns:
            List of human-readable error descriptions
            (e.g. ``"notebook.ipynb: cell 3"``).
        """

        errors: list[str] = []
        for notebook in self.spec.content.notebooks:
            path = self.root / notebook
            try:
                payload = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                errors.append(f"{notebook}: invalid notebook JSON")
                continue

            for index, cell in enumerate(payload.get("cells", [])):
                outputs = cell.get("outputs", [])
                for output in outputs:
                    if output.get("output_type") == "error":
                        errors.append(f"{notebook}: cell {index}")
        return errors
